"""The Phase 3 adapter: outbound call shape, and the post-call webhook.

No network. A fake ElevenLabs client records what would have been sent.
"""

import json
import threading

import pytest

from escalation.config import reload_settings
from escalation.models import CallContext, CallRole
from escalation.providers import elevenlabs as el


class FakeResponse:
    def __init__(self, conversation_id="conv_abc123"):
        self.conversation_id = conversation_id
        self.callSid = "CA000"
        self.success = True


class FakeTwilio:
    """First call is conv_abc123, then conv_2, conv_3 ... so ids stay unique."""

    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def outbound_call(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        n = len(self.calls)
        return FakeResponse("conv_abc123" if n == 1 else f"conv_{n}")


class FakeConvAI:
    def __init__(self):
        self.twilio = FakeTwilio()


class FakeClient:
    def __init__(self):
        self.conversational_ai = FakeConvAI()


@pytest.fixture(autouse=True)
def clear_pending():
    el._PENDING.clear()
    el._ORPHANS.clear()
    yield
    el._PENDING.clear()
    el._ORPHANS.clear()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_default")
    monkeypatch.setenv("ELEVENLABS_PHONE_NUMBER_ID", "phnum_1")
    monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ELEVENLABS_AGENT_ID_KIN", raising=False)
    reload_settings()
    yield
    reload_settings()


def kin_ctx():
    return CallContext(
        alert_id="a_1",
        role=CallRole.KIN,
        to_name="Jess",
        to_phone="+61400000002",
        system_prompt="You are calling {{kin_name}} about {{user_name}}.",
        variables={
            "kin_name": "Jess", "user_name": "Jeff", "stillness_minutes": "15",
            "expected_activity": "walking the dog", "agent_a_outcome": "there was no answer",
        },
        tier=3,
        timeout_s=1.0,
    )


# --- placing the call -----------------------------------------------------


def test_dynamic_variables_are_sent_per_call(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    provider.place_call(kin_ctx())

    sent = client.conversational_ai.twilio.calls[0]
    variables = sent["conversation_initiation_client_data"]["dynamic_variables"]
    assert sent["to_number"] == "+61400000002"
    assert variables["kin_name"] == "Jess"
    assert variables["stillness_minutes"] == "15"


def test_prompt_is_overridden_when_there_is_no_dedicated_agent(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    provider.place_call(kin_ctx())

    override = client.conversational_ai.twilio.calls[0][
        "conversation_initiation_client_data"
    ]["conversation_config_override"]
    # Slots must be filled in, not passed through as {{...}}.
    assert "Jess" in override["agent"]["prompt"]["prompt"]
    assert "{{" not in override["agent"]["prompt"]["prompt"]
    assert override["agent"]["first_message"]


def test_dedicated_agent_skips_the_prompt_override(configured, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_AGENT_ID_KIN", "agent_kin")
    reload_settings()

    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    provider.place_call(kin_ctx())

    sent = client.conversational_ai.twilio.calls[0]
    assert sent["agent_id"] == "agent_kin"
    assert "conversation_config_override" not in sent["conversation_initiation_client_data"]


# --- the webhook ----------------------------------------------------------


def transcription_event(conversation_id="conv_abc123", collection=None, turns=None):
    return json.dumps({
        "type": "post_call_transcription",
        "data": {
            "conversation_id": conversation_id,
            "transcript": turns if turns is not None else [
                {"role": "agent", "message": "Should an ambulance be sent?"},
                {"role": "user", "message": "Yes, please send one."},
            ],
            "analysis": {"transcript_summary": "Kin asked for an ambulance."},
            "data_collection_results": collection or {},
        },
    }).encode()


def test_webhook_resolves_the_waiting_call(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    handle = provider.place_call(kin_ctx())

    raw = transcription_event(collection={
        "outcome": {"value": "ambulance_requested", "rationale": "said yes"},
        "confidence": {"value": 0.94},
    })
    result_box = {}

    def wait():
        result_box["result"] = provider.await_result(handle, timeout_s=2.0)

    waiter = threading.Thread(target=wait)
    waiter.start()
    el.handle_post_call_webhook(raw, signature=None)
    waiter.join(timeout=3)

    result = result_box["result"]
    assert result is not None
    assert result.outcome == "ambulance_requested"
    assert result.confidence == pytest.approx(0.94)
    assert "Yes, please send one." in result.transcript


def test_missing_outcome_field_leaves_classification_to_us(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    provider.place_call(kin_ctx())

    el.handle_post_call_webhook(transcription_event(collection={}), signature=None)
    pending = el._PENDING["conv_abc123"]

    assert pending.result.outcome is None
    assert pending.result.answered is True


def test_call_initiation_failure_is_a_no_answer(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    provider.place_call(kin_ctx())

    raw = json.dumps({
        "type": "call_initiation_failure",
        "data": {"conversation_id": "conv_abc123", "failure_reason": "no-answer"},
    }).encode()
    el.handle_post_call_webhook(raw, signature=None)

    result = el._PENDING["conv_abc123"].result
    assert result.answered is False
    assert "no-answer" in result.error


def test_late_webhook_does_not_resolve_a_call_the_ladder_passed(configured):
    """The ladder has already moved on, so a late outcome must not be acted on."""
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    handle = provider.place_call(kin_ctx())

    assert provider.await_result(handle, timeout_s=0.05) is None  # deadline hit

    el.handle_post_call_webhook(transcription_event(collection={
        "outcome": {"value": "declined"},
    }), signature=None)

    assert "conv_abc123" not in el._PENDING


def test_webhook_for_unknown_conversation_is_ignored(configured):
    el.handle_post_call_webhook(
        transcription_event(conversation_id="conv_never_seen"), signature=None
    )
    assert el._PENDING == {}


def test_transcript_turns_are_flattened_in_order(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)
    provider.place_call(kin_ctx())

    el.handle_post_call_webhook(transcription_event(turns=[
        {"role": "agent", "message": "Hello?"},
        {"role": "user", "message": ""},
        {"role": "user", "message": "Speaking."},
    ]), signature=None)

    transcript = el._PENDING["conv_abc123"].result.transcript
    assert transcript == "agent: Hello?\nuser: Speaking."


# --- issue 5: registration race and pending-entry lifetime ----------------


def test_a_webhook_landing_before_registration_is_not_lost(configured):
    """The conversation_id only exists once the API responds, so a call that
    fails instantly can have its webhook arrive first. That outcome must still
    reach the waiting ladder rather than being dropped as 'unknown'."""
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)

    el.handle_post_call_webhook(
        transcription_event(collection={"outcome": {"value": "declined"}}),
        signature=None,
    )
    handle = provider.place_call(kin_ctx())
    result = provider.await_result(handle, timeout_s=0.5)

    assert result is not None
    assert result.outcome == "declined"


def test_a_failed_place_call_leaves_no_pending_entry(configured):
    client = FakeClient()
    client.conversational_ai.twilio.exc = RuntimeError("twilio 500")
    provider = el.ElevenLabsTwilioProvider(client=client)

    with pytest.raises(RuntimeError):
        provider.place_call(kin_ctx())

    assert el._PENDING == {}


def test_stale_abandoned_entries_are_swept(configured):
    client = FakeClient()
    provider = el.ElevenLabsTwilioProvider(client=client)

    handle = provider.place_call(kin_ctx())
    assert provider.await_result(handle, timeout_s=0.01) is None
    assert "conv_abc123" in el._PENDING  # kept, so a late webhook can be logged

    el._PENDING["conv_abc123"].created_at -= el.PENDING_TTL_SECONDS + 60
    provider.place_call(kin_ctx())  # any new call sweeps

    assert "conv_abc123" not in el._PENDING


def test_orphaned_webhook_results_do_not_accumulate_forever(configured):
    el.handle_post_call_webhook(
        transcription_event(conversation_id="conv_orphan"), signature=None
    )
    assert "conv_orphan" in el._ORPHANS

    el._ORPHANS["conv_orphan"].created_at -= el.PENDING_TTL_SECONDS + 60
    client = FakeClient()
    el.ElevenLabsTwilioProvider(client=client).place_call(kin_ctx())

    assert "conv_orphan" not in el._ORPHANS
