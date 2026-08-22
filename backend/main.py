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
import math
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response)
from pydantic import BaseModel

import engine as engine_mod
import fitbit as fitbit_mod
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


def on_trigger(user_id, scenario, context):
    telephony.place_call(user_id, engine.profiles[user_id], scenario, context)


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


app = FastAPI(title="Telecare Anomaly Detection", lifespan=lifespan)
STATIC = Path(__file__).resolve().parent / "static"


# ------------------------------------------------------------------ telemetry
class Telemetry(BaseModel):
    user_id: str
    timestamp: str
    heart_rate_bpm: int
    step_count_last_5min: int
    motion_intensity: str
    battery_pct: int = 100


@app.post("/ingest")
def ingest(reading: Telemetry):
    try:
        result = engine.ingest(reading.model_dump())
    except KeyError:
        raise HTTPException(404, "unknown user_id %r" % reading.user_id)
    return {"ok": True, **result}


@app.get("/state")
def state():
    snap = engine.snapshot()
    snap["fitbit"] = fitbit.status()
    return snap


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


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


@app.post("/simulate")
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
    if scenario == "wandering":
        # Wandering means movement inside the sleep window, so stamp the
        # sequence at the window's most recent occurrence (e.g. last night)
        # for honest overnight behaviour at any wall-clock demo time.
        h, m = (int(x) for x in
                engine.profiles[uid]["sleep_window"]["start"].split(":"))
        start = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        if start > datetime.now():
            start -= timedelta(days=1)
    else:
        start = datetime.now() - timedelta(seconds=SIM_CADENCE_SEC * (len(seq) - 1))
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


@app.post("/demo/reset-cooldown/{user_id}")
def reset_cooldown(user_id: str):
    if user_id not in engine.profiles:
        raise HTTPException(404, "unknown user_id %r" % user_id)
    engine.reset_cooldown(user_id)
    return {"ok": True, "user_id": user_id}


# --------------------------------------------------------------- Twilio hooks
def _twiml(xml):
    return Response(content=xml, media_type="application/xml")


@app.api_route("/voice/answer", methods=["GET", "POST"])
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


@app.api_route("/voice/handle", methods=["GET", "POST"])
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
