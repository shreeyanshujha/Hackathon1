"""Credential resolution and failure behaviour for the classifier layer.

The SDK resolves credentials from more than ANTHROPIC_API_KEY (an
ANTHROPIC_AUTH_TOKEN, or a profile written by `ant auth login`), so the
classifier must not gate on the env var alone. It must also never raise into
the ladder.
"""

from types import SimpleNamespace

import pytest

from escalation import llm
from escalation.config import reload_settings
from escalation.models import UserCallDecision, UserOutcome


class FakeClient:
    """Stands in for anthropic.Anthropic; `client.messages.parse(...)`."""

    def __init__(self, result=None, exc=None):
        self.attempts = 0
        self.result = result
        self.exc = exc
        self.messages = self
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.attempts += 1
        self.last_kwargs = kwargs
        if self.exc:
            raise self.exc
        return SimpleNamespace(parsed_output=self.result)


@pytest.fixture(autouse=True)
def clean_llm(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(llm, "PROFILE_DIR", tmp_path / "no-such-config")
    reload_settings()
    llm.set_client(None)
    yield
    llm.set_client(None)
    reload_settings()


def test_no_credentials_anywhere_skips_the_network(monkeypatch):
    client = FakeClient(result="unused")
    llm.set_client(client)

    assert llm.claude_decider("system", "hello", UserCallDecision) is None
    assert client.attempts == 0


def test_an_api_key_enables_the_classifier(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reload_settings()
    decision = UserCallDecision(
        outcome=UserOutcome.UNCLEAR, confidence=0.5, summary="ok",
        answered_grounding_question=False,
    )
    client = FakeClient(result=decision)
    llm.set_client(client)

    assert llm.claude_decider("system", "hello", UserCallDecision) is decision
    assert client.attempts == 1


def test_an_ant_auth_profile_also_enables_the_classifier(monkeypatch, tmp_path):
    """`ant auth login` writes a profile the SDK reads with no env var set."""
    profile = tmp_path / "anthropic"
    profile.mkdir()
    monkeypatch.setattr(llm, "PROFILE_DIR", profile)

    decision = UserCallDecision(
        outcome=UserOutcome.UNCLEAR, confidence=0.5, summary="ok",
        answered_grounding_question=False,
    )
    client = FakeClient(result=decision)
    llm.set_client(client)

    assert llm.claude_decider("system", "hello", UserCallDecision) is decision


def test_an_auth_token_also_enables_the_classifier(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    client = FakeClient(result=None)
    llm.set_client(client)

    llm.claude_decider("system", "hello", UserCallDecision)
    assert client.attempts == 1


def test_claude_decider_propagates_client_errors_to_its_caller(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reload_settings()
    llm.set_client(FakeClient(exc=RuntimeError("503 upstream")))

    with pytest.raises(RuntimeError):
        # claude_decider itself propagates; agents._ask is what swallows it.
        llm.claude_decider("system", "hello", UserCallDecision)


def test_agents_swallow_a_failing_classifier(monkeypatch, user_ctx):
    from escalation import agents

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reload_settings()
    llm.set_client(FakeClient(exc=RuntimeError("503 upstream")))

    decision = agents.decide_user_outcome("I'm walking the dog", user_ctx)
    assert decision.outcome == UserOutcome.UNCLEAR.value
    assert decision.source == "fallback"


# --- classifier timeout (issue 4) -----------------------------------------


def test_classifier_call_carries_an_explicit_short_timeout(monkeypatch):
    """The SDK default is minutes; a hung API must not stall the ladder."""
    from escalation.config import settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reload_settings()
    client = FakeClient(result=None)
    llm.set_client(client)

    llm.claude_decider("system", "hello", UserCallDecision)

    assert client.last_kwargs["timeout"] == settings.classifier_timeout
    assert settings.classifier_timeout <= 10


def test_classifier_timeout_is_configurable(monkeypatch):
    from escalation.config import settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLASSIFIER_TIMEOUT", "2.5")
    reload_settings()
    client = FakeClient(result=None)
    llm.set_client(client)

    llm.claude_decider("system", "hello", UserCallDecision)

    assert settings.classifier_timeout == 2.5
    assert client.last_kwargs["timeout"] == 2.5
