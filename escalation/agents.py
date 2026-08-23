"""Agent A (the user) and the shared relay agent (kin and support).

Every function here returns an `AgentDecision` with a fixed shape, so the state
machine never parses prose. Three properties are enforced in code rather than
left to the prompt:

  1. `resolved_ok` is downgraded unless the grounding question was answered and
     confidence clears the tier's bar.
  2. `declined` is downgraded on low confidence, for the same reason in the
     other direction: it is the outcome that stops escalation.
  3. The deterministic fallback has no branch that returns `resolved_ok` or
     `declined`, so an LLM outage can only ever fail upward.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Type

from pydantic import BaseModel

from . import llm
from .config import settings
from .models import (
    AgentDecision,
    CallContext,
    RelayCallDecision,
    RelayOutcome,
    UserCallDecision,
    UserOutcome,
)
from .prompts import render

log = logging.getLogger("escalation.agents")

Decider = Callable[[str, str, Type[BaseModel]], Optional[BaseModel]]


# --- deterministic fallbacks ---------------------------------------------


def fallback_user_outcome(transcript: str) -> UserOutcome:
    """No `resolved_ok` branch exists here, by design."""
    if not (transcript or "").strip():
        return UserOutcome.NO_ANSWER
    return UserOutcome.UNCLEAR


def fallback_relay_outcome(transcript: str) -> RelayOutcome:
    """No `declined` branch exists here, by design."""
    if not (transcript or "").strip():
        return RelayOutcome.NO_ANSWER
    return RelayOutcome.UNCLEAR


# --- validation -----------------------------------------------------------


def validate_user_decision(
    raw: UserCallDecision, ctx: CallContext
) -> AgentDecision:
    bar = settings.resolve_confidence(ctx.tier)
    if raw.outcome == UserOutcome.RESOLVED_OK:
        if not raw.answered_grounding_question:
            return AgentDecision(
                outcome=UserOutcome.UNCLEAR.value,
                confidence=raw.confidence,
                summary=f"Not resolved: no coherent answer to the grounding question. {raw.summary}",
                source="validation",
            )
        if raw.confidence < bar:
            return AgentDecision(
                outcome=UserOutcome.UNCLEAR.value,
                confidence=raw.confidence,
                summary=(
                    f"Not resolved: confidence {raw.confidence:.2f} is under the "
                    f"tier {ctx.tier} bar of {bar:.2f}. {raw.summary}"
                ),
                source="validation",
            )
    return AgentDecision(
        outcome=raw.outcome.value,
        confidence=raw.confidence,
        summary=raw.summary,
        source="llm",
    )


def validate_relay_decision(
    raw: RelayCallDecision, ctx: CallContext
) -> AgentDecision:
    bar = settings.resolve_confidence(ctx.tier)
    if raw.outcome == RelayOutcome.DECLINED and raw.confidence < bar:
        return AgentDecision(
            outcome=RelayOutcome.UNCLEAR.value,
            confidence=raw.confidence,
            summary=(
                f"Not treated as a decline: confidence {raw.confidence:.2f} is under "
                f"the tier {ctx.tier} bar of {bar:.2f}. {raw.summary}"
            ),
            source="validation",
        )
    return AgentDecision(
        outcome=raw.outcome.value,
        confidence=raw.confidence,
        summary=raw.summary,
        source="llm",
    )


# --- Agent A --------------------------------------------------------------


def decide_user_outcome(
    transcript: Optional[str],
    ctx: CallContext,
    *,
    timed_out: bool = False,
    decider: Optional[Decider] = None,
) -> AgentDecision:
    """One of resolved_ok, no_answer, unclear, timeout."""
    if timed_out:
        return AgentDecision(
            outcome=UserOutcome.TIMEOUT.value,
            confidence=1.0,
            summary="No clear outcome inside the time limit.",
            source="machine",
        )
    if not (transcript or "").strip():
        return AgentDecision(
            outcome=UserOutcome.NO_ANSWER.value,
            confidence=1.0,
            summary=f"No answer from {ctx.to_name}.",
            source="machine",
        )

    system = render(ctx.system_prompt, ctx.variables) + llm.JUDGE_SUFFIX + llm.GROUNDING_NOTE
    raw = _ask(decider, system, transcript, UserCallDecision)
    if raw is None:
        outcome = fallback_user_outcome(transcript)
        return AgentDecision(
            outcome=outcome.value,
            confidence=0.0,
            summary="Classifier unavailable; escalating rather than resolving.",
            source="fallback",
        )
    return validate_user_decision(raw, ctx)


# --- Agent B and the support fallback (one implementation) ----------------


def decide_relay_outcome(
    transcript: Optional[str],
    ctx: CallContext,
    *,
    system_prompt: str,
    timed_out: bool = False,
    decider: Optional[Decider] = None,
) -> AgentDecision:
    """One of ambulance_requested, declined, no_answer, unclear.

    Agent B has no `timeout` outcome, so a call that runs out of time is a
    `no_answer`: move on to the next person.
    """
    if timed_out or not (transcript or "").strip():
        return AgentDecision(
            outcome=RelayOutcome.NO_ANSWER.value,
            confidence=1.0,
            summary=f"No answer from {ctx.to_name}.",
            source="machine",
        )

    system = render(system_prompt, ctx.variables) + llm.JUDGE_SUFFIX
    raw = _ask(decider, system, transcript, RelayCallDecision)
    if raw is None:
        outcome = fallback_relay_outcome(transcript)
        return AgentDecision(
            outcome=outcome.value,
            confidence=0.0,
            summary="Classifier unavailable; no decision recorded.",
            source="fallback",
        )
    return validate_relay_decision(raw, ctx)


def _ask(decider, system, transcript, schema):
    fn = decider or llm.claude_decider
    try:
        return fn(system, transcript, schema)
    except Exception as exc:  # noqa: BLE001 - never let a classifier break the ladder
        log.warning("classifier failed (%s); falling back", exc)
        return None
