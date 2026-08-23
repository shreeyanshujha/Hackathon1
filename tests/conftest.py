import pytest

from escalation.models import Alert, CallContext, CallRole, Detail, Kin, SupportContact, UserProfile


@pytest.fixture
def jeff_alert() -> Alert:
    return Alert(
        alert_id="a_test_1",
        user=UserProfile(
            name="Jeff",
            age=72,
            on_beta_blocker=True,
            hr_baseline=58,
            expected_activity="walking the dog",
            phone="+61400000001",
        ),
        reason="still_with_rising_hr",
        detail=Detail(stillness_minutes=15, hr_now=88),
        tier=3,
        kin=[Kin(name="Jess", phone="+61400000002")],
        support_contact=SupportContact(phone="+61400000009"),
    )


@pytest.fixture
def two_kin_alert(jeff_alert) -> Alert:
    """The demo runs with one kin. The ladder still has to walk a longer list,
    so the tests that exercise walking build their own alert rather than
    relying on the demo's shape."""
    jeff_alert.kin.append(Kin(name="Tom", phone="+61400000003"))
    return jeff_alert


@pytest.fixture
def user_ctx(jeff_alert) -> CallContext:
    return CallContext(
        alert_id=jeff_alert.alert_id,
        role=CallRole.USER,
        to_name="Jeff",
        to_phone="+61400000001",
        system_prompt="(agent a prompt)",
        variables={"user_name": "Jeff"},
        tier=3,
        timeout_s=10.0,
    )


@pytest.fixture
def kin_ctx(jeff_alert) -> CallContext:
    return CallContext(
        alert_id=jeff_alert.alert_id,
        role=CallRole.KIN,
        to_name="Jess",
        to_phone="+61400000002",
        system_prompt="(agent b prompt)",
        variables={"kin_name": "Jess", "user_name": "Jeff"},
        tier=3,
        timeout_s=10.0,
    )


@pytest.fixture(autouse=True)
def hermetic_environment(tmp_path, monkeypatch):
    """Pin the environment the suite runs in.

    escalation.api calls load_dotenv() at import, so a populated .env reaches
    the tests. With CALL_PROVIDER=elevenlabs that means the ladder places real
    outbound Twilio calls, and with a live ANTHROPIC_API_KEY the transcript
    classifier makes real API calls — neither of which a test run should do.
    Both are pinned here so the suite stays offline and deterministic no
    matter what is in .env. Tests that want a credential set one themselves.
    """
    from escalation import config, llm

    monkeypatch.setenv("TRANSITION_LOG", str(tmp_path / "transitions.jsonl"))
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("CALL_PROVIDER", "dryrun")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(llm, "PROFILE_DIR", tmp_path / "no-such-config")
    config.reload_settings()
    yield
    config.reload_settings()
