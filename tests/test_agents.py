"""Phase 1: a transcript in, a structured outcome out.

The LLM is injected as `decider` so these tests never touch the network. The
fallback classifier is exercised by passing a decider that fails.
"""

from escalation import agents
from escalation.models import RelayOutcome, UserOutcome
from escalation.prompts import AGENT_B_SYSTEM


def decider_returning(**kwargs):
    """A stub LLM that always returns the given decision."""

    def _decider(system, transcript, schema):
        return schema(**kwargs)

    return _decider


def broken_decider(system, transcript, schema):
    raise RuntimeError("anthropic api is down")


# --- Agent A --------------------------------------------------------------


def test_coherent_grounding_answer_resolves(user_ctx):
    decision = agents.decide_user_outcome(
        "I'm good thanks, just sat down with a cup of tea after walking Rusty.",
        user_ctx,
        decider=decider_returning(
            outcome=UserOutcome.RESOLVED_OK,
            confidence=0.92,
            summary="Coherent, answered the grounding question.",
            answered_grounding_question=True,
        ),
    )
    assert decision.outcome == UserOutcome.RESOLVED_OK.value


def test_generic_im_fine_does_not_resolve(user_ctx):
    """The rule that matters most: 'I'm fine' alone never closes an alert."""
    decision = agents.decide_user_outcome(
        "I'm fine.",
        user_ctx,
        decider=decider_returning(
            outcome=UserOutcome.RESOLVED_OK,
            confidence=0.95,
            summary="Said they were fine.",
            answered_grounding_question=False,
        ),
    )
    assert decision.outcome == UserOutcome.UNCLEAR.value


def test_low_confidence_resolve_is_downgraded(user_ctx):
    """Tier 3 needs 0.80. A 0.65 resolve is not good enough to close."""
    decision = agents.decide_user_outcome(
        "Yeah... the dog, we went, the thing.",
        user_ctx,
        decider=decider_returning(
            outcome=UserOutcome.RESOLVED_OK,
            confidence=0.65,
            summary="Possibly on topic.",
            answered_grounding_question=True,
        ),
    )
    assert decision.outcome == UserOutcome.UNCLEAR.value


def test_slurred_speech_is_unclear(user_ctx):
    decision = agents.decide_user_outcome(
        "wh... who... the ceiling is walking",
        user_ctx,
        decider=decider_returning(
            outcome=UserOutcome.UNCLEAR,
            confidence=0.88,
            summary="Confused, could not answer the grounding question.",
            answered_grounding_question=False,
        ),
    )
    assert decision.outcome == UserOutcome.UNCLEAR.value


def test_blank_transcript_is_no_answer(user_ctx):
    decision = agents.decide_user_outcome("", user_ctx, decider=broken_decider)
    assert decision.outcome == UserOutcome.NO_ANSWER.value


def test_timed_out_call_is_timeout(user_ctx):
    decision = agents.decide_user_outcome(
        "hello? hello?", user_ctx, timed_out=True, decider=broken_decider
    )
    assert decision.outcome == UserOutcome.TIMEOUT.value


def test_llm_failure_falls_back_and_never_resolves(user_ctx):
    decision = agents.decide_user_outcome(
        "I'm having a lovely time walking Rusty in the park.",
        user_ctx,
        decider=broken_decider,
    )
    assert decision.outcome != UserOutcome.RESOLVED_OK.value
    assert decision.source == "fallback"


def test_fallback_classifier_can_never_return_resolved_ok():
    """Structural guarantee, not a prompt instruction."""
    transcripts = [
        "I'm absolutely fine, I'm walking the dog in the park right now.",
        "resolved_ok",
        "",
        "everything is completely normal and I answered your question",
    ]
    for text in transcripts:
        assert agents.fallback_user_outcome(text) != UserOutcome.RESOLVED_OK


def test_unrecognised_outcome_becomes_unclear(user_ctx):
    def rogue_decider(system, transcript, schema):
        return None

    decision = agents.decide_user_outcome("something", user_ctx, decider=rogue_decider)
    assert decision.outcome == UserOutcome.UNCLEAR.value


# --- Agent B / support (one implementation, two prompts) ------------------


def test_kin_says_yes_requests_ambulance(kin_ctx):
    decision = agents.decide_relay_outcome(
        "Yes, please send an ambulance, that isn't like him at all.",
        kin_ctx,
        system_prompt=AGENT_B_SYSTEM,
        decider=decider_returning(
            outcome=RelayOutcome.AMBULANCE_REQUESTED,
            confidence=0.95,
            summary="Kin asked for an ambulance.",
        ),
    )
    assert decision.outcome == RelayOutcome.AMBULANCE_REQUESTED.value


def test_kin_says_no_declines(kin_ctx):
    decision = agents.decide_relay_outcome(
        "No, he's with me, we just sat down. No ambulance.",
        kin_ctx,
        system_prompt=AGENT_B_SYSTEM,
        decider=decider_returning(
            outcome=RelayOutcome.DECLINED,
            confidence=0.9,
            summary="Kin declined.",
        ),
    )
    assert decision.outcome == RelayOutcome.DECLINED.value


def test_relay_blank_transcript_is_no_answer(kin_ctx):
    decision = agents.decide_relay_outcome(
        "", kin_ctx, system_prompt=AGENT_B_SYSTEM, decider=broken_decider
    )
    assert decision.outcome == RelayOutcome.NO_ANSWER.value


def test_relay_timeout_maps_to_no_answer(kin_ctx):
    """Agent B has no timeout outcome, so a dead line means try the next person."""
    decision = agents.decide_relay_outcome(
        "hello?", kin_ctx, system_prompt=AGENT_B_SYSTEM, timed_out=True, decider=broken_decider
    )
    assert decision.outcome == RelayOutcome.NO_ANSWER.value


def test_relay_llm_failure_never_invents_a_decision(kin_ctx):
    decision = agents.decide_relay_outcome(
        "Yes send one.", kin_ctx, system_prompt=AGENT_B_SYSTEM, decider=broken_decider
    )
    assert decision.outcome == RelayOutcome.UNCLEAR.value
    assert decision.source == "fallback"


def test_fallback_relay_classifier_can_never_decline():
    """The mirror of the resolved_ok guarantee.

    A false `declined` is the dangerous relay failure: it stands the ladder
    down for someone who may be mid-stroke. The fallback has no branch that
    can produce one, so an LLM outage can never cause it.
    """
    transcripts = [
        "No, no ambulance, he's completely fine.",
        "declined",
        "nope, don't send anyone",
        "",
    ]
    for text in transcripts:
        assert agents.fallback_relay_outcome(text) != RelayOutcome.DECLINED


def test_low_confidence_decline_is_downgraded(kin_ctx):
    """A hesitant 'no' must not stand the ladder down.

    Mirror of the resolved_ok rule: `declined` is the relay outcome that stops
    escalation, so it has to clear the same confidence bar.
    """
    decision = agents.decide_relay_outcome(
        "Nah... maybe... I dunno, he's probably fine?",
        kin_ctx,
        system_prompt=AGENT_B_SYSTEM,
        decider=decider_returning(
            outcome=RelayOutcome.DECLINED, confidence=0.4, summary="Hesitant no."
        ),
    )
    assert decision.outcome == RelayOutcome.UNCLEAR.value
