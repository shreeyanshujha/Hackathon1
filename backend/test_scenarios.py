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
def clean_state(monkeypatch):
    # The suite exercises the legacy direct-call path unless a test opts in
    # to the escalation handoff explicitly (a developer .env may set this).
    monkeypatch.delenv("ESCALATION_URL", raising=False)
    # Zero-credential mode regardless of the developer's .env: a real
    # TWILIO_AUTH_TOKEN arms webhook signature verification, which the
    # signature test opts into explicitly.
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
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
    main.demo_state["escalation_scenario"] = None
    main.recent_escalations.clear()
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


# ------------------------------------------------------- escalation handoff
def test_trigger_hands_off_to_escalation_service(monkeypatch):
    import escalation_bridge
    sent = {}

    def fake_hand_off(user_id, profile, scenario, context,
                      ladder_scenario=None):
        sent["payload"] = escalation_bridge.build_alert(
            user_id, profile, scenario, context)
        return {"alert_id": sent["payload"]["alert_id"],
                "state": "detected", "status_url": "/alerts/x"}

    monkeypatch.setenv("ESCALATION_URL", "http://escalation.test")
    monkeypatch.setattr(main.escalation_bridge, "hand_off", fake_hand_off)
    client.post("/simulate?scenario=acute")
    assert events("escalation_handoff", "usr_demo_a")
    assert not call_events("usr_demo_a")   # the ladder owns the calls now
    payload = sent["payload"]
    assert payload["user"]["name"] == "Jeff"
    assert payload["tier"] == 3            # urgency high -> tier 3
    assert payload["detail"]["hr_now"] is not None
    assert payload["kin"][0]["phone"] == \
        engine.profiles["usr_demo_a"]["kin_phone"]
    assert payload["support_contact"]["phone"] == \
        engine.profiles["usr_demo_a"]["responder_phone"]


def test_arm_escalation_scenario_validates():
    assert client.post("/demo/escalation-scenario?scenario=kin") \
        .json()["armed"] == "kin"
    assert client.post("/demo/escalation-scenario?scenario=default") \
        .json()["armed"] is None
    assert client.post("/demo/escalation-scenario?scenario=bogus") \
        .status_code == 400


def test_armed_scenario_rides_on_handoff(monkeypatch):
    seen = {}

    def fake_hand_off(user_id, profile, scenario, context, ladder_scenario=None):
        seen["ladder"] = ladder_scenario
        return {"alert_id": "alr-test", "state": "detected",
                "status_url": "/alerts/alr-test"}

    monkeypatch.setenv("ESCALATION_URL", "http://escalation.test")
    monkeypatch.setattr(main.escalation_bridge, "hand_off", fake_hand_off)
    client.post("/demo/escalation-scenario?scenario=kin")
    client.post("/simulate?scenario=acute")
    assert seen["ladder"] == "kin"
    assert "alr-test" in main.recent_escalations


def test_console_instant_trigger(monkeypatch):
    seen = {}

    def fake_hand_off(user_id, profile, scenario, context, ladder_scenario=None):
        seen.update(user_id=user_id, scenario=scenario, ladder=ladder_scenario,
                    context=context)
        return {"alert_id": "alr-instant", "state": "detected",
                "status_url": "/alerts/alr-instant"}

    monkeypatch.setenv("ESCALATION_URL", "http://escalation.test")
    monkeypatch.setattr(main.escalation_bridge, "hand_off", fake_hand_off)
    r = client.post("/escalation/trigger?user_id=usr_demo_b&scenario=resolved")
    assert r.status_code == 200 and r.json()["alert_id"] == "alr-instant"
    assert seen["user_id"] == "usr_demo_b" and seen["ladder"] == "resolved"
    assert seen["context"]["urgency"] == "high"
    assert "alr-instant" in main.recent_escalations
    assert events("escalation_handoff", "usr_demo_b")
    # Unknown user and unconfigured service both fail loudly, not silently.
    assert client.post("/escalation/trigger?user_id=usr_nope").status_code == 404


def test_escalation_recent_degrades_without_service():
    r = client.get("/escalation/recent")
    assert r.status_code == 200
    assert r.json() == {"armed": None, "service_ok": False, "runs": []}


def test_console_page_served():
    r = client.get("/console")
    assert r.status_code == 200 and "Presenter" in r.text


def test_escalation_unreachable_falls_back_to_direct_call(monkeypatch):
    # Port 9 refuses connections immediately: a dead sidecar must never
    # swallow an alert — the legacy kin call still goes out.
    monkeypatch.setenv("ESCALATION_URL", "http://127.0.0.1:9")
    client.post("/simulate?scenario=acute")
    assert events("escalation_unreachable", "usr_demo_a")
    assert call_events("usr_demo_a")


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


# ------------------------------------------------------------- profile overlay
def test_profiles_local_overlay_applied(tmp_path):
    # A synced UserBaselineProfile is persisted to profiles.local.json and
    # must survive a restart: RuleEngine applies it over profiles.json.
    import json
    import shutil
    from pathlib import Path
    base = Path(main.__file__).resolve().parent
    shutil.copy(base / "profiles.json", tmp_path / "profiles.json")
    (tmp_path / "profiles.local.json").write_text(json.dumps(
        {"usr_live": {"name": "Edna Krabappel", "age": 78},
         "usr_ghost": {"name": "Nobody"}}))
    eng = engine_mod.RuleEngine(
        profiles_path=tmp_path / "profiles.json",
        overlay_path=tmp_path / "profiles.local.json")
    assert eng.profiles["usr_live"]["name"] == "Edna Krabappel"
    assert eng.profiles["usr_live"]["age"] == 78
    # Fields the overlay doesn't set survive, and unknown users (no data
    # source to feed them) are not invented.
    assert eng.profiles["usr_live"]["source"] == "fitbit"
    assert "usr_ghost" not in eng.profiles


def test_missing_overlay_is_harmless(tmp_path):
    import shutil
    from pathlib import Path
    base = Path(main.__file__).resolve().parent
    shutil.copy(base / "profiles.json", tmp_path / "profiles.json")
    eng = engine_mod.RuleEngine(
        profiles_path=tmp_path / "profiles.json",
        overlay_path=tmp_path / "profiles.local.json")
    assert eng.profiles["usr_live"]["name"]


# ------------------------------------------------------- module 1 profile sync
def _baseline(consent=True):
    days = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")
    routine = {day: [] for day in days}
    routine["monday"] = [{"activity": "Morning walk",
                          "expectedTime": "10:00", "expectedDuration": 45}]
    return {
        "schemaVersion": 1,
        "demographics": {"name": "Edna Krabappel", "sex": "female",
                         "dob": "1948-03-15", "livingSituation": "lives_alone"},
        "emergencyContacts": [
            {"name": "Sarah", "relationship": "Daughter",
             "phone": "+61400000001", "isPrimary": True}],
        "sleep": {"typicalWake": "06:45", "typicalSleep": "21:30",
                  "napPattern": None},
        "weeklyRoutine": routine,
        "hobbies": [], "mobilityLevel": "walking_aid",
        "lifestyle": {"diet": None,
                      "smoking": {"status": False, "frequency": None},
                      "alcohol": {"status": False, "frequency": None}},
        "healthContext": [], "medicationCount": None,
        "consent": {"monitoringConsent": consent, "sharedWith": ["nextOfKin"]},
        "deviceId": None, "completedAt": "2026-08-22T10:00:00.000Z",
    }


@pytest.fixture
def live_card_restored(monkeypatch, tmp_path):
    """Overlay writes go to tmp, and the live card is restored afterwards."""
    import json
    monkeypatch.setattr(engine_mod, "LOCAL_PROFILES_PATH",
                        tmp_path / "profiles.local.json")
    before = json.loads(json.dumps(engine.profiles["usr_live"]))
    yield tmp_path / "profiles.local.json"
    engine.profiles["usr_live"] = before


def test_profile_sync_updates_live_card(live_card_restored):
    import json
    resting_before = engine.profiles["usr_live"]["resting_hr_bpm"]
    r = client.post("/profile", json=_baseline())
    assert r.status_code == 200 and r.json()["user_id"] == "usr_live"

    live = engine.profiles["usr_live"]
    assert live["name"] == "Edna Krabappel"
    assert live["sleep_window"] == {"start": "21:30", "end": "06:45"}
    assert live["kin_name"] == "Sarah"
    assert live["routine"][0]["activity"] == "Morning walk"
    # What onboarding can't know is preserved: data source, calibrated
    # resting HR, and the cloud-lag threshold overrides.
    assert live["source"] == "fitbit"
    assert live["resting_hr_bpm"] == resting_before
    assert live["missing_data_min"] == 15
    # Persisted to the gitignored overlay so a restart keeps the person.
    overlay = json.loads(live_card_restored.read_text())
    assert overlay["usr_live"]["name"] == "Edna Krabappel"
    assert events("profile_synced", "usr_live")


def test_profile_sync_refuses_unconsented(live_card_restored):
    r = client.post("/profile", json=_baseline(consent=False))
    assert r.status_code == 403
    assert engine.profiles["usr_live"]["name"] != "Edna Krabappel"
    assert not live_card_restored.exists()


def test_profile_sync_rejects_malformed(live_card_restored):
    bad = _baseline()
    bad["emergencyContacts"] = []
    assert client.post("/profile", json=bad).status_code == 422


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
    assert client.post("/profile", json=_baseline()).status_code == 401
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
