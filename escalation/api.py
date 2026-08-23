"""HTTP surface: alert intake, the demo trigger, the audit trail, and the
ElevenLabs post-call webhook.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from dotenv import load_dotenv

# Settings reads the environment at import time, so .env has to be loaded
# before any escalation module is. load_dotenv does not override variables
# already exported in the shell.
load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from .config import settings  # noqa: E402
from .generator import make_alert
from .machine import new_alert_id, run_alert
from .models import Alert, AlertRun, AlertState, SupportContact
from .providers.base import CallProvider
from .providers.dryrun import DryRunProvider, ScriptedCall  # noqa: E402
from .scenarios import ScenarioName, script_for  # noqa: E402

log = logging.getLogger("escalation.api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="Emergency Escalation Agent System")

# Demo-scale storage. A real deployment needs a database; the JSONL audit log
# is the durable record either way.
RUNS: dict[str, AlertRun] = {}
_lock = threading.Lock()


def build_provider(
    scenario: Optional[ScenarioName] = None,
    provider: Optional[str] = None,
) -> CallProvider:
    """The live provider ignores scenarios; the phone decides what happens.

    `provider` overrides CALL_PROVIDER for one alert — the demo console runs
    scripted dry runs all day and fires a single real call on demand.
    """
    if (provider or settings.call_provider) == "elevenlabs":
        from .providers.elevenlabs import ElevenLabsTwilioProvider

        return ElevenLabsTwilioProvider()
    script = script_for(scenario) if scenario else {}
    return DryRunProvider(script, default=ScriptedCall(answered=False))


def _register_pending(alert: Alert) -> AlertRun:
    """Publish the run before it starts, so its progress is observable.

    A re-fired alert id starts a fresh run rather than appending to the old one.
    """
    with _lock:
        run = RUNS.get(alert.alert_id)
        if run is None or run.is_terminal:
            run = AlertRun(alert=alert, state=AlertState.DETECTED)
            RUNS[alert.alert_id] = run
        return run


def _execute(
    alert: Alert,
    scenario: Optional[ScenarioName] = None,
    provider: Optional[str] = None,
) -> AlertRun:
    run = _register_pending(alert)
    chosen = build_provider(scenario) if provider is None else \
        build_provider(scenario, provider)
    return run_alert(alert, chosen, run=run)


@app.get("/health")
def health():
    return {
        "ok": True,
        "provider": settings.call_provider,
        "demo_mode": settings.demo_mode,
        "classifier": settings.classifier_model,
        "classifier_key_present": bool(settings.anthropic_api_key),
    }


@app.post("/alerts")
def intake(
    alert: Alert,
    background: BackgroundTasks,
    wait: bool = False,
    scenario: Optional[ScenarioName] = None,
    provider: Optional[str] = None,
):
    """Accept an alert from the detection module and start the ladder."""
    if provider not in (None, "dryrun", "elevenlabs"):
        raise HTTPException(400, "provider must be dryrun or elevenlabs")
    if alert.support_contact is None:
        alert.support_contact = SupportContact(phone=settings.support_phone)

    if wait:
        return _execute(alert, scenario, provider).summary()

    _register_pending(alert)
    background.add_task(_execute, alert, scenario, provider)
    return {
        "alert_id": alert.alert_id,
        "state": AlertState.DETECTED.value,
        "status_url": f"/alerts/{alert.alert_id}",
    }


@app.post("/trigger")
def trigger(
    background: BackgroundTasks,
    wait: bool = False,
    tier: int = 3,
    scenario: ScenarioName = ScenarioName.KIN,
):
    """Fire a test alert on demand. No setup, no body.

    Defaults to the headline demo path: no answer from the user, then kin asks
    for an ambulance. `?scenario=support` shows the kin-to-support fallthrough.
    """
    alert = make_alert(alert_id=new_alert_id(), tier=tier, randomise=True)
    log.info(
        "\n=== TRIGGER %s | %s, tier %d | still %d min | HR %d -> %d ===",
        alert.alert_id,
        alert.user.name,
        alert.tier,
        alert.detail.stillness_minutes,
        alert.user.hr_baseline,
        alert.detail.hr_now,
    )
    return intake(alert, background, wait=wait, scenario=scenario)


@app.get("/alerts")
def list_alerts():
    with _lock:
        return [run.summary() for run in RUNS.values()]


@app.get("/alerts/{alert_id}")
def get_alert(alert_id: str):
    run = RUNS.get(alert_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown alert_id")
    return {
        **run.summary(),
        "alert": run.alert.model_dump(mode="json"),
        "transitions": [t.model_dump(mode="json") for t in run.transitions],
        "calls": [c.model_dump(mode="json") for c in run.calls],
    }


@app.post("/webhooks/elevenlabs/post-call")
async def elevenlabs_post_call(request: Request):
    """Resolve a call that ElevenLabs has finished."""
    from .providers.elevenlabs import handle_post_call_webhook

    raw = await request.body()
    signature = request.headers.get("elevenlabs-signature")
    try:
        resolved = handle_post_call_webhook(raw, signature)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)

    if not resolved:
        return JSONResponse({"status": "ignored"}, status_code=202)
    return {"status": "resolved", "conversation_id": resolved}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """A page for the demo, so the ladder is visible without the terminal."""
    with _lock:
        runs = list(RUNS.values())

    colour = {
        "resolved": "#1a7f37", "escalated": "#cf222e", "unresolved": "#9a6700",
        "calling_user": "#0969da", "calling_kin": "#0969da",
        "calling_support": "#8250df", "detected": "#57606a",
    }
    rows = []
    for run in reversed(runs):
        for t in run.transitions:
            tint = colour.get(t.to_state.value, "#57606a")
            sim = " <strong>SIMULATED</strong>" if t.simulated else ""
            rows.append(
                f"<tr><td class=ts>{t.ts}</td><td class=id>{t.alert_id}</td>"
                f"<td>{t.from_state.value} &rarr; <span style='color:{tint};font-weight:600'>"
                f"{t.to_state.value}</span></td><td>{t.outcome or ''}</td>"
                f"<td class=detail>{t.detail}{sim}</td></tr>"
            )
    body = "\n".join(rows) or "<tr><td colspan=5>No alerts yet. POST /trigger.</td></tr>"
    return f"""<!doctype html><meta charset=utf-8>
<title>Escalation log</title>
<meta http-equiv=refresh content=2>
<style>
 body{{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;margin:2rem;color:#1f2328}}
 h1{{font-size:15px;letter-spacing:.02em}}
 table{{border-collapse:collapse;width:100%}}
 td,th{{padding:.35rem .6rem;border-bottom:1px solid #d1d9e0;text-align:left;vertical-align:top}}
 th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#57606a}}
 .ts,.id{{color:#57606a;white-space:nowrap}}
 .detail{{color:#57606a}}
 @media (prefers-color-scheme:dark){{
   body{{background:#0d1117;color:#e6edf3}} td,th{{border-color:#30363d}}
   .ts,.id,.detail{{color:#8b949e}}
 }}
</style>
<h1>Escalation log &middot; provider={settings.call_provider} &middot; demo_mode={settings.demo_mode}</h1>
<table><tr><th>time<th>alert<th>transition<th>outcome<th>detail</tr>
{body}
</table>"""
