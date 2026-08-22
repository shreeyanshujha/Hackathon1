"""FastAPI app: telemetry ingestion, rule engine, demo surface, Twilio webhooks.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import contextlib
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import engine as engine_mod
import telephony as tel

load_dotenv(Path(__file__).resolve().parent / ".env")

WATCHDOG_INTERVAL_SEC = 60  # per spec: watchdog sweep cadence
SIM_CADENCE_SEC = 5         # simulated readings arrive at device batch cadence

# ------------------------------------------------------------------ wiring
engine = engine_mod.RuleEngine()
telephony = tel.Telephony(log_event=lambda uid, t, m, sev="info":
                          engine.log_event(uid, t, m, sev))


def on_trigger(user_id, scenario, context):
    telephony.place_call(user_id, engine.profiles[user_id], scenario, context)


engine.on_trigger = on_trigger


@contextlib.asynccontextmanager
async def lifespan(_app):
    async def watchdog():
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
            try:
                engine.check_missing_data()
            except Exception as exc:  # never let the watchdog die silently
                engine.log_event("system", "watchdog_error", str(exc), "info")

    task = asyncio.create_task(watchdog())
    yield
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
    return engine.snapshot()


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


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
    "wandering": ("usr_demo_d", _seq_wandering),
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

    seq = seq_fn(engine.profiles[uid])
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
    note = ("now stop all telemetry and wait ~%d min for the watchdog"
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
    routine = engine.current_routine(user_id)
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
