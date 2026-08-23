"""Phase 2: the escalation ladder.

These tests drive the state machine with a scripted provider that supplies
outcomes directly, the same way the ElevenLabs post-call webhook does. No LLM
and no network is involved, so what is under test is the ladder itself.
"""

from escalation.machine import run_alert
from escalation.models import AlertState, RelayOutcome, UserOutcome
from escalation.providers.dryrun import DryRunProvider, ScriptedCall

JEFF = "+61400000001"
JESS = "+61400000002"
TOM = "+61400000003"       # only in the two-kin tests
SUPPORT = "+61400000009"

NO_ANSWER = ScriptedCall(answered=False)
TIMED_OUT = ScriptedCall(timeout=True)


def ok(outcome, transcript="(scripted)", confidence=0.95, grounded=True):
    return ScriptedCall(
        answered=True, outcome=outcome, transcript=transcript,
        confidence=confidence, grounded=grounded,
    )


def states(run):
    return [t.to_state for t in run.transitions]


# --- the happy path -------------------------------------------------------


def test_user_answers_coherently_and_alert_resolves(jeff_alert):
    provider = DryRunProvider({JEFF: ok(UserOutcome.RESOLVED_OK.value, "Just walked Rusty.")})
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.RESOLVED
    assert states(run) == [AlertState.CALLING_USER, AlertState.RESOLVED]
    assert run.ambulance_simulated is False


# --- user unreachable, kin decides ---------------------------------------


def test_no_answer_from_user_then_kin_requests_ambulance(jeff_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        JESS: ok(RelayOutcome.AMBULANCE_REQUESTED.value, "Yes, send one."),
    })
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.ESCALATED
    assert states(run) == [
        AlertState.CALLING_USER, AlertState.CALLING_KIN, AlertState.ESCALATED,
    ]
    assert run.ambulance_simulated is True


def test_unclear_user_then_kin_declines_resolves(jeff_alert):
    provider = DryRunProvider({
        JEFF: ok(UserOutcome.UNCLEAR.value, "the ceiling is walking", grounded=False),
        JESS: ok(RelayOutcome.DECLINED.value, "No, he's right here with me."),
    })
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.RESOLVED
    assert run.ambulance_simulated is False


def test_user_timeout_escalates_to_kin(jeff_alert):
    provider = DryRunProvider({
        JEFF: TIMED_OUT,
        JESS: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    user_call = run.transitions[0]
    assert user_call.to_state == AlertState.CALLING_USER
    assert run.transitions[1].outcome == UserOutcome.TIMEOUT.value
    assert run.state == AlertState.ESCALATED


# --- walking the kin list -------------------------------------------------


def test_first_kin_no_answer_walks_to_second(two_kin_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        JESS: NO_ANSWER,
        TOM: ok(RelayOutcome.AMBULANCE_REQUESTED.value, "Yes please."),
    })
    run = run_alert(two_kin_alert, provider)

    assert run.state == AlertState.ESCALATED
    assert states(run).count(AlertState.CALLING_KIN) == 2
    assert [c.to_name for c in run.calls] == ["Jeff", "Jess", "Tom"]


def test_kin_unclear_walks_to_next_kin(two_kin_alert):
    """An unclear kin call is treated like no answer: ask someone else."""
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        JESS: ok(RelayOutcome.UNCLEAR.value, "...crackle... what? ...", confidence=0.3),
        TOM: ok(RelayOutcome.DECLINED.value, "No, I'm with him."),
    })
    run = run_alert(two_kin_alert, provider)

    assert run.state == AlertState.RESOLVED
    assert states(run).count(AlertState.CALLING_KIN) == 2


# --- the support rung -----------------------------------------------------


def test_kin_exhausted_falls_through_to_support(jeff_alert):
    """One kin means the list is exhausted after a single unanswered call."""
    provider = DryRunProvider({
        JEFF: NO_ANSWER, JESS: NO_ANSWER,
        SUPPORT: ok(RelayOutcome.AMBULANCE_REQUESTED.value, "Yes, dispatch."),
    })
    run = run_alert(jeff_alert, provider)

    assert len(jeff_alert.kin) == 1
    assert states(run).count(AlertState.CALLING_KIN) == 1
    assert run.state == AlertState.ESCALATED
    assert AlertState.CALLING_SUPPORT in states(run)
    assert run.calls[-1].to_phone == SUPPORT
    assert run.ambulance_simulated is True


def test_single_kin_exhaustion_is_named_in_the_audit_detail(jeff_alert):
    """The demo's one kin should be named, not counted as 'All 1 kin'."""
    provider = DryRunProvider({JEFF: NO_ANSWER, JESS: NO_ANSWER, SUPPORT: NO_ANSWER})
    run = run_alert(jeff_alert, provider)

    row = [t for t in run.transitions if t.to_state == AlertState.CALLING_SUPPORT][0]
    assert "Jess" in row.detail
    assert "All 1 kin" not in row.detail


def test_support_declines_resolves(jeff_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER, JESS: NO_ANSWER,
        SUPPORT: ok(RelayOutcome.DECLINED.value, "No, we've got eyes on him."),
    })
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.RESOLVED
    assert run.ambulance_simulated is False


def test_support_unreachable_is_the_only_route_to_unresolved(jeff_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER, JESS: NO_ANSWER, SUPPORT: NO_ANSWER,
    })
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.UNRESOLVED
    assert run.ambulance_simulated is False


def test_support_unclear_escalates(jeff_alert):
    """No rung above support, so ambiguity there resolves upward to an ambulance."""
    provider = DryRunProvider({
        JEFF: NO_ANSWER, JESS: NO_ANSWER,
        SUPPORT: ok(RelayOutcome.UNCLEAR.value, "...breaking up...", confidence=0.2),
    })
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.ESCALATED
    assert run.ambulance_simulated is True


def test_alert_with_no_kin_goes_straight_to_support(jeff_alert):
    jeff_alert.kin = []
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        SUPPORT: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    assert AlertState.CALLING_KIN not in states(run)
    assert AlertState.CALLING_SUPPORT in states(run)
    assert run.state == AlertState.ESCALATED


# --- audit trail ----------------------------------------------------------


def test_every_transition_is_logged_with_id_and_timestamp(jeff_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        JESS: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    assert run.transitions
    for t in run.transitions:
        assert t.alert_id == jeff_alert.alert_id
        assert t.ts
        assert t.from_state != t.to_state or t.to_state == AlertState.CALLING_KIN


def test_every_call_is_recorded_with_a_transcript_summary(jeff_alert):
    provider = DryRunProvider({
        JEFF: ok(UserOutcome.UNCLEAR.value, "mumbling", grounded=False),
        JESS: ok(RelayOutcome.DECLINED.value, "No thanks."),
    })
    run = run_alert(jeff_alert, provider)

    assert len(run.calls) == 2
    for call in run.calls:
        assert call.decision is not None
        assert call.decision.summary


# --- ambulance safety -----------------------------------------------------


def test_ambulance_is_labelled_simulated_in_the_log(jeff_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        JESS: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    escalation_row = [t for t in run.transitions if t.to_state == AlertState.ESCALATED][0]
    assert escalation_row.simulated is True
    assert "SIMULATED" in escalation_row.detail.upper()


def test_no_emergency_number_is_ever_dialled(jeff_alert):
    """The ladder must never place a call to an emergency service."""
    provider = DryRunProvider({
        JEFF: NO_ANSWER, JESS: NO_ANSWER,
        SUPPORT: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    emergency = {"000", "111", "112", "911", "999", "+61000"}
    dialled = {c.to_phone for c in run.calls} | set(provider.dialled)
    assert not (dialled & emergency)
    assert run.ambulance_simulated is True


def test_provider_error_is_treated_as_no_answer_and_ladder_continues(jeff_alert):
    provider = DryRunProvider({
        JEFF: ScriptedCall(answered=False, error="twilio 500"),
        JESS: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    assert run.state == AlertState.ESCALATED
    assert run.calls[0].error == "twilio 500"


# --- malformed provider outcomes (issue 2) --------------------------------


def test_unusable_user_outcome_falls_back_to_classifying_the_transcript(jeff_alert):
    """ElevenLabs data collection is free text: it can emit 'OK' or 'resolved'.

    An outcome the enum does not recognise must behave exactly like no outcome
    at all, not kill the alert mid-run.
    """
    provider = DryRunProvider({
        JEFF: ScriptedCall(answered=True, outcome="OK", transcript="I'm fine."),
        JESS: ok(RelayOutcome.AMBULANCE_REQUESTED.value),
    })
    run = run_alert(jeff_alert, provider)

    assert run.calls[0].decision.outcome == UserOutcome.UNCLEAR.value
    assert run.state == AlertState.ESCALATED


def test_unusable_relay_outcome_does_not_crash_the_ladder(jeff_alert):
    provider = DryRunProvider({
        JEFF: NO_ANSWER,
        JESS: ScriptedCall(answered=True, outcome="yes please!", transcript="Yes, send one."),
        SUPPORT: ok(RelayOutcome.DECLINED.value, "No, he's fine."),
    })
    run = run_alert(jeff_alert, provider)

    assert run.calls[1].decision.outcome == RelayOutcome.UNCLEAR.value
    assert run.state == AlertState.RESOLVED


def test_an_unusable_outcome_never_resolves_by_accident(jeff_alert):
    """'resolved' is the most likely near-miss string, and must not close an alert."""
    provider = DryRunProvider({
        JEFF: ScriptedCall(answered=True, outcome="resolved", transcript="Walking Rusty."),
        JESS: NO_ANSWER, SUPPORT: NO_ANSWER,
    })
    run = run_alert(jeff_alert, provider)

    assert run.state != AlertState.RESOLVED


# --- live visibility (issue 3) --------------------------------------------


def test_run_alert_mutates_a_run_that_is_already_registered(jeff_alert):
    """Callers can register the run first, then watch it fill in."""
    from escalation.models import AlertRun

    existing = AlertRun(alert=jeff_alert)
    returned = run_alert(jeff_alert, DryRunProvider({}), run=existing)

    assert returned is existing
    assert existing.transitions
    assert existing.state == AlertState.UNRESOLVED
