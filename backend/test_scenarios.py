"""End-to-end tests for the rule engine + demo surface + telephony branches.

Run:  pytest test_scenarios.py -v
Twilio env vars are intentionally absent -> calls/SMS are simulated and
logged, which is what we assert on.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import engine as engine_mod
import main
from main import app, engine

client = TestClient(app)


def events(etype=None, user_id=None):
    return [e for e in engine.events
            if (etype is None or e["type"] == etype)
            and (user_id is None or e["user_id"] == user_id)]


def call_events(user_id=None):
    return events("call_simulated", user_id) + events("call_placed", user_id)


@pytest.fixture(autouse=True)
def clean_state():
    # Force "awake now" for the daytime-scenario users regardless of when the
    # tests run: a zero-width sleep window 12 hours away.
    far = (datetime.now() + timedelta(hours=12)).strftime("%H:%M")
    for uid in ("usr_demo_a", "usr_demo_b", "usr_demo_c"):
        engine.profiles[uid]["sleep_window"] = {"start": far, "end": far}
    # Wandering still works: /simulate stamps its sequence inside the target's
    # sleep window (here the zero-width far-away minute), whatever the clock says.
    for uid in list(engine.users):
        engine.users[uid] = engine_mod.UserState()
    engine.events.clear()
    main.mock_pause_until.clear()
    telephony_ctx = main.telephony.call_context
    telephony_ctx.clear()
    yield


def user_state(uid):
    return client.get("/state").json()["users"][uid]


# --------------------------------------------------------------- ingest schema
def test_ingest_exact_schema():
    r = client.post("/ingest", json={
        "user_id": "usr_demo_a", "timestamp": datetime.now().isoformat(),
        "heart_rate_bpm": 72, "step_count_last_5min": 120,
        "motion_intensity": "moderate", "battery_pct": 84})
    assert r.status_code == 200 and r.json()["ok"]


def test_ingest_accepts_null_heart_rate():
    # Sensor dropout: the watch may have no HR reading (off-wrist, poor
    # contact). The contract is null — never 0 — and the engine must treat it
    # as unknown, not as a real (low) heart rate.
    r = client.post("/ingest", json={
        "user_id": "usr_demo_a", "timestamp": datetime.now().isoformat(),
        "heart_rate_bpm": None, "step_count_last_5min": 0,
        "motion_intensity": "stationary", "battery_pct": 84})
    assert r.status_code == 200 and r.json()["ok"]
    assert user_state("usr_demo_a")["latest"]["heart_rate_bpm"] is None


def test_ingest_unknown_user_404():
    r = client.post("/ingest", json={
        "user_id": "usr_nope", "timestamp": datetime.now().isoformat(),
        "heart_rate_bpm": 72, "step_count_last_5min": 0,
        "motion_intensity": "stationary", "battery_pct": 84})
    assert r.status_code == 404


# ------------------------------------------------------------------- scenarios
def test_acute_triggers_one_call():
    r = client.post("/simulate?scenario=acute")
    body = r.json()
    assert body["final"]["status"] == "ALERT"
    assert body["final"]["classification"].startswith("acute")
    assert len(call_events("usr_demo_a")) == 1


def test_immobility_triggers_one_call():
    r = client.post("/simulate?scenario=immobility")
    assert r.json()["final"]["classification"].startswith("immobility")
    assert len(call_events("usr_demo_b")) == 1


def test_wandering_triggers_one_call():
    r = client.post("/simulate?scenario=wandering")
    assert r.json()["final"]["classification"].startswith("wandering")
    assert len(call_events("usr_demo_c")) == 1


def test_normal_logs_only_no_call():
    r = client.post("/simulate?scenario=normal")
    assert r.json()["final"]["status"] == "NORMAL"
    assert call_events("usr_demo_a") == []
    assert events("normal_routine", "usr_demo_a")


# ----------------------------------------------------- simulation timestamping
SLEEP = {"sleep_window": {"start": "22:30", "end": "06:30"}}


def test_waking_scenarios_stamped_outside_sleep_window_at_night():
    # Demoing "normal" or "immobility" during the profile's sleep window must
    # not stamp movement/stillness inside it (a normal-activity demo at 2am
    # used to fire a false wandering alert).
    asleep_now = datetime(2026, 8, 22, 2, 0)
    for scenario in ("normal", "immobility"):
        start = main.sim_start_time(SLEEP, scenario, 5, asleep_now)
        for i in range(5):
            ts = start + timedelta(seconds=main.SIM_CADENCE_SEC * i)
            assert not engine_mod.in_time_window(ts, "22:30", "06:30"), \
                "%s reading %d stamped inside sleep window" % (scenario, i)


def test_waking_scenarios_backdated_to_now_when_awake():
    awake_now = datetime(2026, 8, 22, 14, 0)
    start = main.sim_start_time(SLEEP, "normal", 5, awake_now)
    assert start + timedelta(seconds=main.SIM_CADENCE_SEC * 4) == awake_now


def test_wandering_stamped_inside_most_recent_sleep_window():
    awake_now = datetime(2026, 8, 22, 14, 0)
    start = main.sim_start_time(SLEEP, "wandering", 7, awake_now)
    assert engine_mod.in_time_window(start, "22:30", "06:30")
    assert start <= awake_now


# ------------------------------------------------------------ cooldown machine
def test_cooldown_suppresses_second_incident():
    client.post("/simulate?scenario=acute")
    # Break the streak (recover), then a fresh acute streak within cooldown.
    client.post("/simulate?scenario=normal")
    client.post("/simulate?scenario=acute")
    assert len(call_events("usr_demo_a")) == 1
    assert events("suppressed", "usr_demo_a")


def test_reset_cooldown_allows_new_call():
    client.post("/simulate?scenario=acute")
    assert user_state("usr_demo_a")["cooldown_sec_remaining"] > 0
    r = client.post("/demo/reset-cooldown/usr_demo_a")
    assert r.status_code == 200
    assert user_state("usr_demo_a")["cooldown_sec_remaining"] == 0
    client.post("/simulate?scenario=acute")
    assert len(call_events("usr_demo_a")) == 2


# --------------------------------------------------------------------- watchdog
def test_missing_data_watchdog_fires_lost_connection():
    client.post("/simulate?scenario=lost_connection")
    limit_min = engine.thresholds["missing_data_min"]
    engine.users["usr_demo_c"].last_seen = (
        datetime.now() - timedelta(minutes=limit_min + 1))
    engine.check_missing_data()
    calls = call_events("usr_demo_c")
    assert len(calls) == 1
    assert "lost_connection" in calls[0]["message"]
    assert user_state("usr_demo_c")["missing_flagged"]
    # Second sweep must not double-fire.
    engine.check_missing_data()
    assert len(call_events("usr_demo_c")) == 1
    # Data resuming clears the flag.
    client.post("/ingest", json={
        "user_id": "usr_demo_c", "timestamp": datetime.now().isoformat(),
        "heart_rate_bpm": 64, "step_count_last_5min": 40,
        "motion_intensity": "moderate", "battery_pct": 70})
    assert not user_state("usr_demo_c")["missing_flagged"]
    assert events("reconnected", "usr_demo_c")


# ------------------------------------------------------------------ voice flow
def test_dtmf_1_escalate_and_notify():
    client.post("/simulate?scenario=acute")
    r = client.post("/voice/answer?user_id=usr_demo_a&scenario=acute")
    assert "Press 1 to escalate and notify" in r.text
    r = client.post("/voice/handle?user_id=usr_demo_a&scenario=acute",
                    data={"Digits": "1"})
    assert "<Dial>" in r.text
    assert events("escalated", "usr_demo_a")
    assert events("sms_simulated", "usr_demo_a")


def test_dtmf_2_stand_down_keeps_cooldown():
    client.post("/simulate?scenario=acute")
    r = client.post("/voice/handle?user_id=usr_demo_a&scenario=acute",
                    data={"Digits": "2"})
    assert "standing down" in r.text.lower()
    assert events("stand_down", "usr_demo_a")
    assert user_state("usr_demo_a")["cooldown_sec_remaining"] > 0


# ------------------------------------------------------------ frozen contract
TRIGGER_EVENT_KEYS = {"scenario", "urgency", "hr_now", "hr_baseline",
                      "duration_sec", "schedule_context", "timestamp"}


def test_get_schedule_context_shape_and_routine():
    profile = {"sleep_window": {"start": "22:00", "end": "06:00"},
               "routine": [{"day": "monday", "start": "10:00", "end": "11:00",
                            "activity": "walking the dog"}]}
    monday_10am = datetime(2026, 8, 17, 10, 30)  # a Monday
    ctx = engine_mod.get_schedule_context(profile, monday_10am)
    assert ctx == {"in_sleep_window": False, "waking_hours": True,
                   "routine_note": "walking the dog"}
    ctx = engine_mod.get_schedule_context(profile, datetime(2026, 8, 17, 23, 0))
    assert ctx["in_sleep_window"] and not ctx["waking_hours"]
    assert ctx["routine_note"] == "asleep"


def test_trigger_event_carries_contract_shape():
    client.post("/simulate?scenario=acute")
    ctx = main.telephony.call_context["usr_demo_a"]["context"]
    assert TRIGGER_EVENT_KEYS <= set(ctx)
    assert ctx["scenario"] == "acute" and ctx["urgency"] == "high"
    assert ctx["hr_baseline"] == engine.profiles["usr_demo_a"]["resting_hr_bpm"]
    assert set(ctx["schedule_context"]) == {"in_sleep_window", "waking_hours",
                                            "routine_note"}


def test_lost_connection_trigger_event_shape():
    client.post("/simulate?scenario=lost_connection")
    limit_min = engine.thresholds["missing_data_min"]
    engine.users["usr_demo_c"].last_seen = (
        datetime.now() - timedelta(minutes=limit_min + 1))
    engine.check_missing_data()
    ctx = main.telephony.call_context["usr_demo_c"]["context"]
    assert TRIGGER_EVENT_KEYS <= set(ctx)
    assert ctx["urgency"] == "low"
    assert ctx["hr_now"] is not None      # last known reading's HR
    assert ctx["duration_sec"] >= limit_min * 60


# ------------------------------------------------------------- data sources
def test_mock_stream_readings_are_benign():
    # 5 minutes of continuous mock data outside the sleep window must never
    # trip a rule: that is the whole contract of the always-on stream.
    profile = engine.profiles["usr_demo_a"]
    now = datetime.now()
    for i in range(60):
        reading = main.make_mock_reading("usr_demo_a", profile,
                                         now + timedelta(seconds=5 * i))
        engine.ingest(reading)
    assert call_events("usr_demo_a") == []
    assert user_state("usr_demo_a")["status"] == "NORMAL"


def test_fitbit_status_endpoint_shape():
    body = client.get("/fitbit/status").json()
    assert {"configured", "authorized", "live_user", "login_path"} <= set(body)
    assert body["live_user"] == "usr_live"
    assert isinstance(body["configured"], bool)


# -------------------------------------------------------------------- security
def _reading():
    return {"user_id": "usr_demo_a", "timestamp": datetime.now().isoformat(),
            "heart_rate_bpm": 72, "step_count_last_5min": 120,
            "motion_intensity": "moderate", "battery_pct": 84}


def test_api_secret_enforced_when_configured(monkeypatch):
    # The demo runs behind a public tunnel; with API_SHARED_SECRET set, the
    # telemetry/demo/state surface must reject requests without the key.
    monkeypatch.setenv("API_SHARED_SECRET", "hunter2")
    assert client.post("/ingest", json=_reading()).status_code == 401
    assert client.post("/simulate?scenario=acute").status_code == 401
    assert client.post("/demo/reset-cooldown/usr_demo_a").status_code == 401
    assert client.get("/state").status_code == 401
    bad = {"X-Api-Key": "wrong"}
    assert client.post("/ingest", json=_reading(), headers=bad).status_code == 401

    ok = {"X-Api-Key": "hunter2"}
    assert client.post("/ingest", json=_reading(), headers=ok).status_code == 200
    assert client.get("/state", headers=ok).status_code == 200
    assert client.post("/simulate?scenario=acute", headers=ok).status_code == 200
    assert client.post("/demo/reset-cooldown/usr_demo_a",
                       headers=ok).status_code == 200
    # The dashboard page itself stays open — static HTML, no data in it.
    assert client.get("/").status_code == 200


def test_twilio_signature_enforced_when_configured(monkeypatch):
    # With an auth token set, the /voice/* webhooks must reject requests that
    # don't carry a valid X-Twilio-Signature — otherwise anyone who knows the
    # tunnel URL can POST Digits=1 and fire the escalation SMS.
    from twilio.request_validator import RequestValidator
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token_123")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example.com")
    client.post("/simulate?scenario=acute")

    path = "/voice/handle?user_id=usr_demo_a&scenario=acute"
    assert client.post(path, data={"Digits": "2"}).status_code == 403
    assert client.post(path, data={"Digits": "2"},
                       headers={"X-Twilio-Signature": "bogus"}).status_code == 403
    assert client.get("/voice/answer?user_id=usr_demo_a"
                      "&scenario=acute").status_code == 403

    sig = RequestValidator("test_token_123").compute_signature(
        "https://demo.example.com" + path, {"Digits": "2"})
    r = client.post(path, data={"Digits": "2"},
                    headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "standing down" in r.text.lower()


def test_voice_webhooks_open_without_twilio_token():
    # Simulated mode (no Twilio credentials): webhooks stay callable so the
    # offline demo and this test suite can exercise the DTMF tree directly.
    r = client.post("/voice/handle?user_id=usr_demo_a&scenario=acute",
                    data={"Digits": "2"})
    assert r.status_code == 200


def test_api_open_without_secret():
    # No API_SHARED_SECRET in the environment -> zero-config demo, no auth.
    assert client.post("/ingest", json=_reading()).status_code == 200
    assert client.get("/state").status_code == 200


# --------------------------------------------------------------------- wording
def test_no_emergency_dispatch_language():
    import pathlib
    banned = ("dial 000", "call 000", "triple zero", "dial 911", "call 911",
              "emergency dispatch", "emergency services")
    for name in ("main.py", "engine.py", "telephony.py", "fitbit.py",
                 "static/dashboard.html", "profiles.json", "config.yaml"):
        text = (pathlib.Path(__file__).parent / name).read_text().lower()
        for phrase in banned:
            assert phrase not in text, "%r found in %s" % (phrase, name)
