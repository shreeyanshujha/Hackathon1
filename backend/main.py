"""FastAPI app: telemetry ingestion, rule engine, demo surface, Twilio webhooks.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Sources feeding the one ingest pipeline:
  - mock streamer (below): the three "source": "mock" profiles stream
    continuously so the dashboard is always alive
  - Fitbit Web API poller (fitbit.py): the "source": "fitbit" profile is the
    wearer's real watch
  - the on-watch developer-bridge app (hackathon-app/) posting to /ingest
  - the /simulate scenario buttons
"""

import asyncio
import contextlib
import json
import math
import random
import secrets as pysecrets
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from dotenv import load_dotenv
from fastapi import (Depends, FastAPI, Form, Header, HTTPException, Query,
                     Request)
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response)
from pydantic import BaseModel

import engine as engine_mod
import escalation_bridge
import fitbit as fitbit_mod
import profile_bridge as bridge
import telephony as tel

load_dotenv(Path(__file__).resolve().parent / ".env")

WATCHDOG_INTERVAL_SEC = 60  # per spec: watchdog sweep cadence
SIM_CADENCE_SEC = 5         # simulated readings arrive at device batch cadence
MOCK_CADENCE_SEC = 5        # continuous mock stream cadence
SIM_PAUSE_SEC = 90          # mock stream stays quiet this long after a simulate
                            # so the injected alert state stays visible

# ------------------------------------------------------------------ wiring
engine = engine_mod.RuleEngine()
telephony = tel.Telephony(log_event=lambda uid, t, m, sev="info":
                          engine.log_event(uid, t, m, sev))


# Presenter-console demo state: the armed ladder outcome rides on the next
# handoff (forced dry-run script), and recent alert ids feed the live pane.
demo_state = {"escalation_scenario": None}
recent_escalations = deque(maxlen=8)
LADDER_SCENARIOS = ("kin", "support", "resolved", "unclear", "unresolved")


def on_trigger(user_id, scenario, context):
    profile = engine.profiles[user_id]
    if escalation_bridge.escalation_url():
        try:
            ack = escalation_bridge.hand_off(
                user_id, profile, scenario, context,
                ladder_scenario=demo_state["escalation_scenario"])
            recent_escalations.appendleft(ack["alert_id"])
            engine.log_event(
                user_id, "escalation_handoff",
                "Handed to escalation agents — Agent A calling %s, then kin "
                "%s (alert %s)" % (profile["name"], profile["kin_name"],
                                   ack["alert_id"]),
                severity="alert")
            return
        except Exception as exc:
            engine.log_event(
                user_id, "escalation_unreachable",
                "Escalation service unreachable (%s) — falling back to the "
                "direct kin call" % exc, severity="alert")
    telephony.place_call(user_id, profile, scenario, context)


engine.on_trigger = on_trigger

LIVE_USER_ID = next((uid for uid, p in engine.profiles.items()
                     if p.get("source") == "fitbit"), None)


def _set_resting_hr(bpm):
    profile = engine.profiles[LIVE_USER_ID]
    if profile["resting_hr_bpm"] != bpm:
        profile["resting_hr_bpm"] = bpm
        engine.log_event(LIVE_USER_ID, "fitbit_calibrated",
                         "Resting HR calibrated from Fitbit: %d bpm" % bpm)


fitbit = fitbit_mod.FitbitBridge(
    user_id=LIVE_USER_ID,
    ingest_fn=engine.ingest,
    log_event=engine.log_event,
    set_resting_hr=_set_resting_hr,
)


# ------------------------------------------------------------- mock streaming
# Readings are deliberately benign at any wall-clock time: asleep inside the
# profile's sleep window (still, no steps), gently active outside it (enough
# steps that the immobility rule can never streak). Alerts stay owned by the
# /simulate buttons, which pause a user's stream while their scenario plays.
mock_pause_until = {}
_mock_started = time.monotonic()
_mock_phase = {}
_mock_burst_until = {}
_rng = random.Random()


def pause_mock_stream(user_id, seconds):
    until = datetime.now() + timedelta(seconds=seconds)
    current = mock_pause_until.get(user_id)
    if current is None or until > current:
        mock_pause_until[user_id] = until


def make_mock_reading(user_id, profile, now=None):
    now = now or datetime.now()
    rest = profile["resting_hr_bpm"]
    phase = _mock_phase.setdefault(user_id, _rng.uniform(0, math.tau))
    wobble = 6 * math.sin(time.monotonic() / 90 + phase)
    asleep = engine_mod.in_time_window(
        now, profile["sleep_window"]["start"], profile["sleep_window"]["end"])

    if asleep:
        hr = rest - 4 + 0.5 * wobble + _rng.gauss(0, 1.5)
        steps, motion = _rng.randint(0, 2), "stationary"
    else:
        bursting = _mock_burst_until.get(user_id, 0) > time.monotonic()
        if not bursting and _rng.random() < 0.03:
            _mock_burst_until[user_id] = time.monotonic() + _rng.uniform(45, 110)
            bursting = True
        if bursting:  # pottering: kettle runs, hallway laps, the garden
            hr = rest + 20 + _rng.gauss(0, 3)
            steps, motion = _rng.randint(60, 140), "moderate"
        else:
            hr = rest + 8 + wobble + _rng.gauss(0, 2)
            steps, motion = _rng.randint(8, 40), "light"

    # Clamp under every alert threshold (acute_hr_bpm, resting + elevated margin).
    hr = int(max(45, min(hr, rest + 27)))
    battery = max(35, 96 - int((time.monotonic() - _mock_started) / 720))
    return {
        "user_id": user_id,
        "timestamp": now.isoformat(timespec="seconds"),
        "heart_rate_bpm": hr,
        "step_count_last_5min": steps,
        "motion_intensity": motion,
        "battery_pct": battery,
    }


async def mock_streamer():
    while True:
        await asyncio.sleep(MOCK_CADENCE_SEC)
        now = datetime.now()
        for user_id, profile in engine.profiles.items():
            if profile.get("source", "mock") != "mock":
                continue
            paused = mock_pause_until.get(user_id)
            if paused and now < paused:
                continue
            try:
                engine.ingest(make_mock_reading(user_id, profile, now))
            except Exception as exc:  # never let the streamer die silently
                engine.log_event("system", "mock_stream_error", str(exc))


@contextlib.asynccontextmanager
async def lifespan(_app):
    async def watchdog():
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
            try:
                engine.check_missing_data()
            except Exception as exc:  # never let the watchdog die silently
                engine.log_event("system", "watchdog_error", str(exc), "info")

    tasks = [asyncio.create_task(watchdog()),
             asyncio.create_task(mock_streamer()),
             asyncio.create_task(fitbit.run())]
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Pulse Point Anomaly Detection", lifespan=lifespan)
STATIC = Path(__file__).resolve().parent / "static"


# ------------------------------------------------------------------- security
# The demo runs behind a public tunnel, so the telemetry/demo/state surface is
# reachable by anyone who learns the URL. Setting API_SHARED_SECRET in .env
# closes it: those endpoints then require a matching X-Api-Key header (the
# dashboard passes it via http://localhost:8000/?key=..., the watch companion
# via its API_KEY constant). Unset = open, for the zero-config local demo.
def require_api_key(x_api_key: str = Header(None)):
    secret = tel.env("API_SHARED_SECRET")
    if secret and not pysecrets.compare_digest(x_api_key or "", secret):
        raise HTTPException(401, "missing or invalid X-Api-Key header")


protected = [Depends(require_api_key)]


# ------------------------------------------------------------------ telemetry
class Telemetry(BaseModel):
    user_id: str
    timestamp: str
    # None = sensor dropout (off-wrist, poor contact) — engine freezes
    # HR-based rules for that reading instead of seeing a real low HR.
    heart_rate_bpm: int | None = None
    step_count_last_5min: int
    motion_intensity: str
    battery_pct: int = 100


@app.post("/ingest", dependencies=protected)
def ingest(reading: Telemetry):
    try:
        result = engine.ingest(reading.model_dump())
    except KeyError:
        raise HTTPException(404, "unknown user_id %r" % reading.user_id)
    return {"ok": True, **result}


@app.get("/state", dependencies=protected)
def state():
    snap = engine.snapshot()
    snap["fitbit"] = fitbit.status()
    return snap


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


# ---------------------------------------------------- module 1 profile sync
@app.post("/profile", dependencies=protected)
def sync_profile(baseline: dict):
    """Accept a Module 1 UserBaselineProfile and apply it to the live card.

    The onboarded person is the watch wearer, so the contract's fields
    (sleep window, weekly routine, contacts, demographics) update usr_live
    in place; source, calibrated resting HR and threshold overrides are
    preserved. The merge is persisted to the gitignored profiles.local.json
    overlay so it survives a restart.
    """
    if LIVE_USER_ID is None:
        raise HTTPException(409, "no live profile card configured")
    try:
        fields = bridge.baseline_to_profile(baseline)
    except bridge.ConsentError as exc:
        raise HTTPException(403, str(exc))
    except bridge.BaselineError as exc:
        raise HTTPException(422, str(exc))

    engine.update_profile(LIVE_USER_ID, fields)
    overlay_path = engine_mod.LOCAL_PROFILES_PATH
    overlay = json.loads(overlay_path.read_text()) if overlay_path.exists() \
        else {}
    overlay[LIVE_USER_ID] = {**overlay.get(LIVE_USER_ID, {}), **fields}
    overlay_path.write_text(json.dumps(overlay, indent=2))
    engine.log_event(
        LIVE_USER_ID, "profile_synced",
        "Baseline profile synced from onboarding app: %s (%s), kin %s — "
        "sleep %s–%s, %d routine entries."
        % (fields["name"], fields["age"], fields["kin_name"],
           fields["sleep_window"]["start"], fields["sleep_window"]["end"],
           len(fields["routine"])))
    return {"ok": True, "user_id": LIVE_USER_ID,
            "profile": engine.profiles[LIVE_USER_ID]}


# ------------------------------------------------------------------- fitbit
@app.get("/fitbit/login")
def fitbit_login():
    if not fitbit.configured():
        return HTMLResponse(fitbit.setup_help_html())
    return RedirectResponse(fitbit.login_url())


@app.get("/fitbit/callback")
def fitbit_callback(code: str = Query(None), state: str = Query(None),
                    error: str = Query(None)):
    if error or not code:
        return HTMLResponse(
            "<h3>Fitbit sign-in did not complete (%s).</h3>"
            "<p><a href='/fitbit/login'>Try again</a></p>" % (error or "no code"))
    try:
        fitbit.exchange_code(code, state)
    except Exception as exc:
        return HTMLResponse(
            "<h3>Fitbit token exchange failed</h3><pre>%s</pre>"
            "<p><a href='/fitbit/login'>Try again</a></p>" % exc)
    return RedirectResponse("/")


@app.get("/fitbit/status")
def fitbit_status():
    return fitbit.status()


# ------------------------------------------------------------------ simulator
# Canned sequences. Each entry: (hr, steps_last_5min, motion_intensity).
# Sequences are injected through the SAME /ingest pipeline with timestamps
# walking up to "now" at device cadence, so sustained-window logic runs
# exactly as it would with live telemetry.
def _seq_acute(profile):
    return [(126, 2, "stationary"), (129, 1, "stationary"), (133, 0, "stationary"),
            (136, 0, "stationary"), (138, 0, "stationary"), (140, 0, "stationary"),
            (141, 0, "stationary"), (142, 0, "stationary")]


def _seq_immobility(profile):
    hr = profile["resting_hr_bpm"]
    return [(hr + d, 0, "stationary") for d in (2, 1, 3, 2, 1, 2, 3, 2, 1, 2)]


def _seq_wandering(profile):
    hr = profile["resting_hr_bpm"] + 28
    return [(hr + d, 380 + 40 * i, "high") for i, d in
            enumerate((0, 2, 4, 3, 5, 4, 6))]


def _seq_normal(profile):
    # Elevated HR while clearly moving: matrix case 1, log-only.
    hr = profile["resting_hr_bpm"] + 45
    return [(hr + d, 420 + 60 * i, "high") for i, d in enumerate((0, 3, 5, 4, 6))]


def _seq_lost_connection(profile):
    # Seed a few healthy readings, then the demoer simply stops sending;
    # the watchdog fires after missing_data_min.
    hr = profile["resting_hr_bpm"]
    return [(hr + 4, 60, "moderate"), (hr + 3, 55, "moderate"), (hr + 5, 62, "moderate")]


SCENARIOS = {
    "acute": ("usr_demo_a", _seq_acute),
    "immobility": ("usr_demo_b", _seq_immobility),
    "wandering": ("usr_demo_c", _seq_wandering),
    "normal": ("usr_demo_a", _seq_normal),
    "lost_connection": ("usr_demo_c", _seq_lost_connection),
}

# Scenarios whose rules only make sense in waking hours; stamping them inside
# the sleep window would misfire other rules (movement at 2am = wandering).
WAKING_SCENARIOS = ("normal", "immobility")


def _most_recent(now, hhmm):
    """Most recent occurrence of a HH:MM time of day, at or before now."""
    h, m = (int(x) for x in hhmm.split(":"))
    occ = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return occ - timedelta(days=1) if occ > now else occ


def sim_start_time(profile, scenario, seq_len, now):
    """Timestamp of a simulated sequence's first reading.

    wandering: inside the sleep window's most recent occurrence.
    normal/immobility: need waking hours — if now is inside the sleep window,
    the sequence ends just before the window began (yesterday evening).
    everything else: backdated so the last reading lands on now.
    """
    window = profile["sleep_window"]
    span = timedelta(seconds=SIM_CADENCE_SEC * (seq_len - 1))
    if scenario == "wandering":
        return _most_recent(now, window["start"])
    end = now
    if scenario in WAKING_SCENARIOS and \
            engine_mod.in_time_window(now, window["start"], window["end"]):
        end = _most_recent(now, window["start"]) - timedelta(seconds=60)
    return end - span


@app.post("/simulate", dependencies=protected)
def simulate(scenario: str = Query(...), user_id: str = Query(None)):
    if scenario not in SCENARIOS:
        raise HTTPException(400, "scenario must be one of %s" % list(SCENARIOS))
    default_user, seq_fn = SCENARIOS[scenario]
    uid = user_id or default_user
    if uid not in engine.profiles:
        raise HTTPException(404, "unknown user_id %r" % uid)

    # Keep the continuous mock stream out of the way while the scenario plays;
    # lost_connection needs silence long enough for the watchdog to notice.
    if engine.profiles[uid].get("source", "mock") == "mock":
        pause = SIM_PAUSE_SEC
        if scenario == "lost_connection":
            pause = engine.thresholds["missing_data_min"] * 60 + 90
        pause_mock_stream(uid, pause)

    seq = seq_fn(engine.profiles[uid])
    start = sim_start_time(engine.profiles[uid], scenario, len(seq),
                           datetime.now())
    results = []
    for i, (hr, steps, motion) in enumerate(seq):
        ts = start + timedelta(seconds=SIM_CADENCE_SEC * i)
        results.append(ingest(Telemetry(
            user_id=uid,
            timestamp=ts.isoformat(timespec="seconds"),
            heart_rate_bpm=hr,
            step_count_last_5min=steps,
            motion_intensity=motion,
            battery_pct=84,
        )))
    note = ("mock stream paused — the watchdog flags lost connection after ~%d min"
            % engine.thresholds["missing_data_min"]
            if scenario == "lost_connection" else None)
    return {"ok": True, "scenario": scenario, "user_id": uid,
            "readings_injected": len(seq), "final": results[-1], "note": note}


@app.post("/demo/reset-cooldown/{user_id}", dependencies=protected)
def reset_cooldown(user_id: str):
    if user_id not in engine.profiles:
        raise HTTPException(404, "unknown user_id %r" % user_id)
    engine.reset_cooldown(user_id)
    return {"ok": True, "user_id": user_id}


# ---------------------------------------------------------- presenter console
# /console drives a forced demo: arm how the agent ladder ends, fire detection
# scenarios, or skip detection entirely. The backend proxies the escalation
# service so the page needs no CORS and no second origin.
@app.get("/console")
def console():
    return FileResponse(STATIC / "console.html")


@app.post("/demo/escalation-scenario", dependencies=protected)
def arm_escalation_scenario(scenario: str = Query(None)):
    """Arm the dry-run script the NEXT ladder run plays. Empty/default clears."""
    if scenario in (None, "", "default"):
        demo_state["escalation_scenario"] = None
    elif scenario in LADDER_SCENARIOS:
        demo_state["escalation_scenario"] = scenario
    else:
        raise HTTPException(400, "scenario must be one of %s"
                            % (LADDER_SCENARIOS,))
    return {"ok": True, "armed": demo_state["escalation_scenario"]}


@app.post("/escalation/trigger", dependencies=protected)
def escalation_trigger(user_id: str = Query("usr_live"),
                       scenario: str = Query(None)):
    """Instant agent call: hand a synthetic acute event straight to the
    ladder, skipping the detection window. For the 30-seconds-left demo."""
    profile = engine.profiles.get(user_id)
    if profile is None:
        raise HTTPException(404, "unknown user_id %r" % user_id)
    if not escalation_bridge.escalation_url():
        raise HTTPException(503, "ESCALATION_URL not configured")
    now = datetime.now()
    state = engine.users[user_id]
    context = {
        "scenario": "acute", "urgency": "high",
        "hr_now": state.last_hr or profile["resting_hr_bpm"] + 74,
        "hr_baseline": profile["resting_hr_bpm"],
        "duration_sec": 12 * 60,
        "schedule_context": engine_mod.get_schedule_context(profile, now),
        "timestamp": now.isoformat(timespec="seconds"),
    }
    ladder = scenario or demo_state["escalation_scenario"]
    try:
        ack = escalation_bridge.hand_off(user_id, profile, "acute", context,
                                         ladder_scenario=ladder)
    except Exception as exc:
        raise HTTPException(502, "escalation service unreachable: %s" % exc)
    recent_escalations.appendleft(ack["alert_id"])
    engine.log_event(
        user_id, "escalation_handoff",
        "Console-triggered agent call for %s (alert %s)"
        % (profile["name"], ack["alert_id"]), severity="alert")
    return {"ok": True, **ack}


@app.get("/escalation/recent", dependencies=protected)
def escalation_recent():
    """Armed outcome + the last few ladder runs, fetched from the service."""
    out = {"armed": demo_state["escalation_scenario"], "service_ok": False,
           "runs": []}
    base = escalation_bridge.escalation_url()
    if not base:
        return out
    try:
        with httpx.Client(base_url=base, timeout=3.0) as client:
            out["service_ok"] = client.get("/health").json().get("ok", False)
            for alert_id in list(recent_escalations):
                r = client.get("/alerts/%s" % alert_id)
                if r.status_code != 200:
                    continue
                run = r.json()
                out["runs"].append({
                    "alert_id": run["alert_id"],
                    "user": run["alert"]["user"]["name"],
                    "state": run["state"],
                    "ambulance_simulated": run["ambulance_simulated"],
                    "transitions": [
                        {"from": t["from_state"], "to": t["to_state"],
                         "outcome": t.get("outcome"),
                         "detail": t.get("detail", "")}
                        for t in run["transitions"]],
                    "calls": [
                        {"role": c["role"], "to_name": c["to_name"],
                         "dry_run": c["dry_run"],
                         "outcome": (c.get("decision") or {}).get("outcome")}
                        for c in run["calls"]],
                })
    except Exception:
        pass  # service down mid-demo: the pane shows the red dot, not a 500
    return out


# --------------------------------------------------------------- Twilio hooks
def _twiml(xml):
    return Response(content=xml, media_type="application/xml")


async def require_twilio_signature(request: Request):
    """403 unless the webhook request is authentically from Twilio.

    Twilio signs the exact URL it requested, which is the public tunnel URL —
    so the check reconstructs it from PUBLIC_BASE_URL, not from the local
    request host. Skipped entirely when no auth token is configured.
    """
    params = dict(await request.form()) if request.method == "POST" else {}
    url = tel.env("PUBLIC_BASE_URL") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    if not tel.valid_twilio_request(url, params,
                                    request.headers.get("X-Twilio-Signature")):
        raise HTTPException(403, "invalid or missing Twilio signature")


@app.api_route("/voice/answer", methods=["GET", "POST"],
               dependencies=[Depends(require_twilio_signature)])
def voice_answer(user_id: str = Query(...), scenario: str = Query(...)):
    profile = engine.profiles.get(user_id)
    if not profile:
        return _twiml(tel.invalid_twiml())
    ctx = telephony.call_context.get(user_id, {}).get("context", {})
    # Prefer the routine_note frozen into the TriggerEvent at trigger time;
    # fall back to a live lookup for calls replayed without one.
    routine = (ctx.get("schedule_context") or {}).get("routine_note") \
        or engine.current_routine(user_id)
    base_url = tel.env("PUBLIC_BASE_URL")
    engine.log_event(user_id, "call_answered",
                     "Kin answered — playing %s script" % scenario)
    return _twiml(tel.answer_twiml(profile, scenario, ctx, routine,
                                   base_url, user_id))


@app.api_route("/voice/handle", methods=["GET", "POST"],
               dependencies=[Depends(require_twilio_signature)])
async def voice_handle(user_id: str = Query(...), scenario: str = Query(...),
                       Digits: str = Form(None)):
    profile = engine.profiles.get(user_id)
    if not profile:
        return _twiml(tel.invalid_twiml())

    if Digits == "1":
        ctx = telephony.call_context.get(user_id, {}).get("context", {})
        engine.log_event(
            user_id, "escalated",
            "Kin pressed 1 — Escalate & Notify: bridging to responder %s and "
            "sending SMS to kin." % tel.responder_number(profile), "alert")
        telephony.send_escalation_sms(user_id, profile, scenario, ctx)
        return _twiml(tel.escalate_twiml(profile))
    if Digits == "2":
        engine.stand_down(user_id)
        return _twiml(tel.stand_down_twiml(profile))
    return _twiml(tel.invalid_twiml())
