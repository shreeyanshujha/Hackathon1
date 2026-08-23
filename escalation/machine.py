"""The escalation ladder.

    detected -> calling_user -> calling_kin[*] -> calling_support -> terminal

The machine owns the routing and nothing else. Deciding what a call meant is
`agents`; placing it is a `CallProvider`. Both relay rungs (kin and support)
run through one implementation, differing only in prompt and callee.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from . import agents
from .audit import AuditLog
from .config import settings
from .models import (
    Alert,
    AlertRun,
    AlertState,
    AgentDecision,
    CallContext,
    CallRecord,
    CallRole,
    Kin,
    RelayCallDecision,
    RelayOutcome,
    SupportContact,
    Transition,
    UserCallDecision,
    UserOutcome,
    utcnow_iso,
)
from .prompts import (
    AGENT_A_OUTCOME_PHRASING,
    AGENT_A_SYSTEM,
    AGENT_B_SYSTEM,
    AGENT_SUPPORT_SYSTEM,
)
from .providers.base import CallProvider, CallResult

log = logging.getLogger("escalation.machine")

SIMULATED_AMBULANCE = (
    "*** SIMULATED AMBULANCE — NO REAL CALL PLACED. "
    "This build never contacts emergency services. ***"
)


def new_alert_id() -> str:
    return f"a_{uuid.uuid4().hex[:8]}"


# --- context builders -----------------------------------------------------


def _base_variables(alert: Alert, agent_a_outcome: Optional[str] = None) -> dict[str, str]:
    variables = {
        "user_name": alert.user.name,
        "stillness_minutes": str(alert.detail.stillness_minutes),
        "hr_baseline": str(alert.user.hr_baseline),
        "hr_now": str(alert.detail.hr_now),
        "expected_activity": alert.user.expected_activity,
    }
    if agent_a_outcome:
        variables["agent_a_outcome"] = AGENT_A_OUTCOME_PHRASING.get(
            agent_a_outcome, agent_a_outcome
        )
    return variables


def _user_context(alert: Alert) -> CallContext:
    return CallContext(
        alert_id=alert.alert_id,
        role=CallRole.USER,
        to_name=alert.user.name,
        to_phone=alert.user.phone or settings.jeff_phone,
        system_prompt=AGENT_A_SYSTEM,
        variables=_base_variables(alert),
        tier=alert.tier,
        timeout_s=settings.call_timeout(alert.tier),
    )


def _kin_context(alert: Alert, kin: Kin, agent_a_outcome: str) -> CallContext:
    variables = _base_variables(alert, agent_a_outcome)
    variables["kin_name"] = kin.name
    return CallContext(
        alert_id=alert.alert_id,
        role=CallRole.KIN,
        to_name=kin.name,
        to_phone=kin.phone,
        system_prompt=AGENT_B_SYSTEM,
        variables=variables,
        tier=alert.tier,
        timeout_s=settings.call_timeout(alert.tier),
    )


def _support_context(alert: Alert, support: SupportContact, agent_a_outcome: str) -> CallContext:
    return CallContext(
        alert_id=alert.alert_id,
        role=CallRole.SUPPORT,
        to_name=support.name,
        to_phone=support.phone,
        system_prompt=AGENT_SUPPORT_SYSTEM,
        variables=_base_variables(alert, agent_a_outcome),
        tier=alert.tier,
        timeout_s=settings.call_timeout(alert.tier),
    )


# --- one call -------------------------------------------------------------


def _decision_from_provider(
    result: CallResult, ctx: CallContext
) -> Optional[AgentDecision]:
    """A provider that extracted its own outcome still goes through validation.

    Returns None when the outcome string is not one this ladder recognises.
    ElevenLabs data collection is free text, so an agent can emit "OK" or
    "resolved" instead of `resolved_ok`. An unusable outcome must behave
    exactly like no outcome at all — classify the transcript instead — rather
    than raising and killing the alert mid-run.
    """
    summary = (result.transcript or "").strip()[:160] or "(outcome supplied by provider)"
    try:
        if ctx.role is CallRole.USER:
            raw = UserCallDecision(
                outcome=UserOutcome(result.outcome),
                confidence=result.confidence,
                summary=summary,
                answered_grounding_question=result.grounded,
            )
            return agents.validate_user_decision(raw, ctx)
        relay = RelayCallDecision(
            outcome=RelayOutcome(result.outcome),
            confidence=result.confidence,
            summary=summary,
        )
        return agents.validate_relay_decision(relay, ctx)
    except ValueError:
        log.warning(
            "provider returned unrecognised outcome %r on the %s call; "
            "classifying the transcript instead",
            result.outcome,
            ctx.role.value,
        )
        return None


def _place(
    ctx: CallContext,
    provider: CallProvider,
    decider,
    relay_prompt: Optional[str] = None,
) -> tuple[AgentDecision, CallRecord]:
    record = CallRecord(
        alert_id=ctx.alert_id,
        role=ctx.role,
        to_name=ctx.to_name,
        to_phone=ctx.to_phone,
        dry_run=getattr(provider, "name", "") == "dryrun",
    )

    result: Optional[CallResult]
    try:
        handle = provider.place_call(ctx)
        result = provider.await_result(handle, ctx.timeout_s)
    except Exception as exc:  # noqa: BLE001 - a broken provider must not end the ladder
        log.warning("provider failed for %s: %s", ctx.to_name, exc)
        record.error = str(exc)
        result = CallResult(answered=False, error=str(exc))

    timed_out = result is None
    answered = bool(result and result.answered)
    transcript = result.transcript if (result and answered) else ""
    record.transcript = transcript
    if result and result.error:
        record.error = result.error

    decision: Optional[AgentDecision] = None
    if result and answered and result.outcome:
        decision = _decision_from_provider(result, ctx)

    if decision is None:
        if ctx.role is CallRole.USER:
            decision = agents.decide_user_outcome(
                transcript, ctx, timed_out=timed_out, decider=decider
            )
        else:
            decision = agents.decide_relay_outcome(
                transcript,
                ctx,
                system_prompt=relay_prompt or AGENT_B_SYSTEM,
                timed_out=timed_out,
                decider=decider,
            )

    record.decision = decision
    return decision, record


# --- the ladder -----------------------------------------------------------


def _move(
    run: AlertRun,
    to_state: AlertState,
    audit: AuditLog,
    *,
    outcome: Optional[str] = None,
    detail: str = "",
    transcript_summary: Optional[str] = None,
    simulated: bool = False,
) -> None:
    transition = Transition(
        alert_id=run.alert.alert_id,
        from_state=run.state,
        to_state=to_state,
        outcome=outcome,
        detail=detail,
        transcript_summary=transcript_summary,
        simulated=simulated,
    )
    run.state = to_state
    run.transitions.append(transition)
    audit.record(transition)


def _escalate(run: AlertRun, audit: AuditLog, outcome: str, reason: str, summary: Optional[str]) -> AlertRun:
    run.ambulance_simulated = True
    _move(
        run,
        AlertState.ESCALATED,
        audit,
        outcome=outcome,
        detail=f"{reason} {SIMULATED_AMBULANCE}",
        transcript_summary=summary,
        simulated=True,
    )
    return _finish(run)


def _finish(run: AlertRun) -> AlertRun:
    run.finished_at = utcnow_iso()
    return run


def run_alert(
    alert: Alert,
    provider: CallProvider,
    *,
    decider=None,
    audit: Optional[AuditLog] = None,
    run: Optional[AlertRun] = None,
) -> AlertRun:
    """Drive one alert from `detected` to a terminal state.

    Pass `run` to fill in an AlertRun the caller has already published (the API
    registers it in RUNS first), so status endpoints see transitions accumulate
    during the run rather than all at once when it ends. Still returned, so
    callers that do not care are unaffected.
    """
    audit = audit if audit is not None else AuditLog()
    run = run if run is not None else AlertRun(alert=alert)

    # --- rung 1: the user
    _move(
        run,
        AlertState.CALLING_USER,
        audit,
        detail=(
            f"{alert.user.name} still for {alert.detail.stillness_minutes} min, "
            f"HR {alert.user.hr_baseline} -> {alert.detail.hr_now}, tier {alert.tier}"
        ),
    )
    decision, record = _place(_user_context(alert), provider, decider)
    run.calls.append(record)

    if decision.outcome == UserOutcome.RESOLVED_OK.value:
        _move(
            run,
            AlertState.RESOLVED,
            audit,
            outcome=decision.outcome,
            detail=f"{alert.user.name} answered coherently.",
            transcript_summary=decision.summary,
        )
        return _finish(run)

    agent_a_outcome = decision.outcome
    carried = decision

    # --- rung 2: next of kin, in order
    for kin in alert.kin:
        _move(
            run,
            AlertState.CALLING_KIN,
            audit,
            outcome=carried.outcome,
            detail=f"Calling {kin.name}.",
            transcript_summary=carried.summary,
        )
        carried, record = _place(
            _kin_context(alert, kin, agent_a_outcome),
            provider,
            decider,
            relay_prompt=AGENT_B_SYSTEM,
        )
        run.calls.append(record)

        if carried.outcome == RelayOutcome.AMBULANCE_REQUESTED.value:
            return _escalate(run, audit, carried.outcome, f"{kin.name} asked for an ambulance.", carried.summary)
        if carried.outcome == RelayOutcome.DECLINED.value:
            _move(
                run,
                AlertState.RESOLVED,
                audit,
                outcome=carried.outcome,
                detail=f"{kin.name} declined an ambulance.",
                transcript_summary=carried.summary,
            )
            return _finish(run)
        # no_answer or unclear: try the next person

    # --- rung 3: the on-call support line, the last rung
    support = alert.support_contact
    if support is None and settings.support_phone:
        support = SupportContact(phone=settings.support_phone)
    if not alert.kin:
        exhausted = "No kin listed."
    elif len(alert.kin) == 1:
        exhausted = f"{alert.kin[0].name} was the only kin listed and gave no decision."
    else:
        exhausted = f"All {len(alert.kin)} kin tried without a decision."

    if support is None or not support.phone:
        # No support rung configured: anything past kin is a human's job, so
        # the chain ends here and human operators take over.
        _move(
            run,
            AlertState.UNRESOLVED,
            audit,
            outcome=carried.outcome,
            detail=exhausted + " No support line configured — human operators "
                               "take over from here.",
            transcript_summary=carried.summary,
        )
        return _finish(run)

    _move(
        run,
        AlertState.CALLING_SUPPORT,
        audit,
        outcome=carried.outcome,
        detail=exhausted,
        transcript_summary=carried.summary,
    )
    decision, record = _place(
        _support_context(alert, support, agent_a_outcome),
        provider,
        decider,
        relay_prompt=AGENT_SUPPORT_SYSTEM,
    )
    run.calls.append(record)

    if decision.outcome == RelayOutcome.AMBULANCE_REQUESTED.value:
        return _escalate(run, audit, decision.outcome, "Support asked for an ambulance.", decision.summary)
    if decision.outcome == RelayOutcome.UNCLEAR.value:
        # Nothing above support to escalate to, so ambiguity resolves upward here.
        return _escalate(
            run, audit, decision.outcome,
            "No clear decision at the final rung; resolving upward.", decision.summary,
        )
    if decision.outcome == RelayOutcome.DECLINED.value:
        _move(
            run,
            AlertState.RESOLVED,
            audit,
            outcome=decision.outcome,
            detail="Support declined an ambulance.",
            transcript_summary=decision.summary,
        )
        return _finish(run)

    _move(
        run,
        AlertState.UNRESOLVED,
        audit,
        outcome=decision.outcome,
        detail="Support unreachable. Escalation chain exhausted with no decision.",
        transcript_summary=decision.summary,
    )
    return _finish(run)
