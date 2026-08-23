"""The HTTP surface: intake, /trigger, the audit trail, the dashboard."""

import pytest
from fastapi.testclient import TestClient

from escalation.api import RUNS, app
from escalation.models import AlertState

BRIEF_PAYLOAD = {
    "alert_id": "a_123",
    "user": {
        "name": "Jeff",
        "age": 72,
        "on_beta_blocker": True,
        "hr_baseline": 58,
        "expected_activity": "walking the dog",
    },
    "reason": "still_with_rising_hr",
    "detail": {"stillness_minutes": 15, "hr_now": 88},
    "tier": 3,
    "kin": [
        {"name": "Jess", "phone": "+61400000002"},
        {"name": "Tom", "phone": "+61400000003"},
    ],
    "support_contact": {"phone": "+61400000009"},
}


@pytest.fixture
def client():
    RUNS.clear()
    return TestClient(app)


def test_trigger_needs_no_setup_and_runs_a_full_ladder(client):
    body = client.post("/trigger?wait=true").json()

    assert body["state"] in {s.value for s in (
        AlertState.RESOLVED, AlertState.ESCALATED, AlertState.UNRESOLVED)}
    assert body["transitions"] >= 3


def test_intake_accepts_the_detection_module_contract(client):
    body = client.post("/alerts?wait=true", json=BRIEF_PAYLOAD).json()

    assert body["alert_id"] == "a_123"
    assert body["state"] == AlertState.UNRESOLVED.value  # nobody answers in dry run


def test_support_contact_defaults_when_the_payload_omits_it(client):
    payload = {k: v for k, v in BRIEF_PAYLOAD.items() if k != "support_contact"}
    payload["alert_id"] = "a_nosupport"
    client.post("/alerts?wait=true", json=payload)

    run = RUNS["a_nosupport"]
    assert run.alert.support_contact is not None
    assert run.calls[-1].to_phone == run.alert.support_contact.phone


def test_audit_trail_is_retrievable_per_alert(client):
    client.post("/alerts?wait=true", json=BRIEF_PAYLOAD)
    detail = client.get("/alerts/a_123").json()

    assert [t["from_state"] for t in detail["transitions"]][0] == "detected"
    for t in detail["transitions"]:
        assert t["alert_id"] == "a_123"
        assert t["ts"]
    assert len(detail["calls"]) == 4  # Jeff, Jess, Tom, support


def test_unknown_alert_is_a_404(client):
    assert client.get("/alerts/nope").status_code == 404


def test_tier_only_changes_timeouts_not_the_flow(client):
    tier1 = client.post("/trigger?wait=true&tier=1").json()
    tier3 = client.post("/trigger?wait=true&tier=3").json()

    def shape(summary):
        return [t["to_state"] for t in client.get(f"/alerts/{summary['alert_id']}").json()["transitions"]]

    assert shape(tier1) == shape(tier3)


def test_dashboard_renders_the_transitions(client):
    client.post("/alerts?wait=true", json=BRIEF_PAYLOAD)
    page = client.get("/")

    assert page.status_code == 200
    assert "a_123" in page.text
    assert "calling_support" in page.text


def test_health_reports_the_active_provider(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["provider"] == "dryrun"


def test_trigger_defaults_to_the_headline_demo_path(client):
    """Definition of done: no answer from the user through to a simulated
    ambulance, with no manual setup."""
    body = client.post("/trigger?wait=true").json()

    assert body["state"] == AlertState.ESCALATED.value
    assert body["ambulance_simulated"] is True


def test_trigger_can_select_the_support_fallthrough(client):
    body = client.post("/trigger?wait=true&scenario=support").json()
    detail = client.get(f"/alerts/{body['alert_id']}").json()
    states = [t["to_state"] for t in detail["transitions"]]

    assert "calling_support" in states
    assert body["state"] == AlertState.ESCALATED.value


def test_trigger_rejects_an_unknown_scenario(client):
    assert client.post("/trigger?wait=true&scenario=nonsense").status_code == 422


def test_simulated_ambulance_is_labelled_in_the_api_payload(client):
    body = client.post("/trigger?wait=true").json()
    detail = client.get(f"/alerts/{body['alert_id']}").json()
    row = [t for t in detail["transitions"] if t["to_state"] == "escalated"][0]

    assert row["simulated"] is True
    assert "SIMULATED" in row["detail"].upper()


# --- live visibility (issue 3) --------------------------------------------


class SlowProvider:
    """Blocks inside the first call so the run can be inspected mid-flight."""

    name = "slow"

    def __init__(self, gate):
        self.gate = gate
        self.dialled = []

    def place_call(self, ctx):
        from escalation.providers.base import CallHandle

        self.dialled.append(ctx.to_phone)
        return CallHandle(call_id="slow", ctx=ctx, provider=self.name)

    def await_result(self, handle, timeout_s):
        from escalation.providers.base import CallResult

        self.gate.wait(5)
        return CallResult(answered=False)


def test_transitions_are_visible_while_the_ladder_is_still_running(monkeypatch):
    """The log page is the demo artefact; it must not sit frozen on `detected`."""
    import threading
    import time

    from escalation import api as api_module
    from escalation.models import Alert

    gate = threading.Event()
    provider = SlowProvider(gate)
    monkeypatch.setattr(api_module, "build_provider", lambda scenario=None: provider)

    RUNS.clear()
    alert = Alert(**BRIEF_PAYLOAD)
    api_module._register_pending(alert)
    worker = threading.Thread(target=api_module._execute, args=(alert,))
    worker.start()
    try:
        deadline = time.time() + 3
        while not provider.dialled and time.time() < deadline:
            time.sleep(0.01)

        in_flight = RUNS[alert.alert_id]
        assert in_flight.state == AlertState.CALLING_USER
        assert len(in_flight.transitions) >= 1
        assert not in_flight.is_terminal
    finally:
        gate.set()
        worker.join(timeout=5)

    assert RUNS[alert.alert_id].is_terminal


def test_provider_override_is_validated_and_dryrun_runs(client):
    # One-alert provider override: the demo console runs scripted dry runs
    # all day and fires a single real call on demand.
    assert client.post("/alerts?provider=bogus", json=BRIEF_PAYLOAD).status_code == 400
    from escalation.config import settings

    payload = dict(BRIEF_PAYLOAD)
    payload["kin"] = [{"name": "Jess", "phone": settings.jess_phone}]
    body = client.post("/alerts?wait=true&provider=dryrun&scenario=kin",
                       json=payload).json()
    assert body["state"] == "escalated" and body["ambulance_simulated"]
