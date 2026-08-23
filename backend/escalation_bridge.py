"""Hand a fired TriggerEvent to the escalation agent service.

The escalation ladder (escalation-service/, branch `escalation-ladder`) owns
everything after detection: Agent A calls the wearer, Agent B the kin list,
then the support fallback — see its README. This module translates our
TriggerEvent contract into its `POST /alerts` payload.

Set ESCALATION_URL in backend/.env (e.g. http://localhost:8100) to route
alerts there; unset, main.py keeps the legacy Twilio DTMF path. If the
service is unreachable the caller falls back to the legacy path too, so a
dead sidecar never swallows an alert.
"""

import os
import time

import httpx

# TriggerEvent urgency -> escalation tier (tier only tightens timeouts and
# the confidence bar over there, it never changes the ladder's flow).
_TIER = {"high": 3, "med": 2, "low": 1}


def escalation_url():
    return (os.environ.get("ESCALATION_URL") or "").rstrip("/")


def build_alert(user_id, profile, scenario, context):
    """Map TriggerEvent + profile onto the escalation service's Alert model."""
    schedule = context.get("schedule_context") or {}
    hr_baseline = context.get("hr_baseline") or profile["resting_hr_bpm"]
    return {
        "alert_id": "alr-%s-%s-%d" % (user_id, scenario, int(time.time())),
        "user": {
            "name": profile["name"],
            "age": profile["age"],
            "hr_baseline": hr_baseline,
            "expected_activity": schedule.get("routine_note") or "at home",
            "phone": profile.get("user_phone"),
        },
        "reason": scenario,
        "detail": {
            "stillness_minutes": int((context.get("duration_sec") or 0) // 60),
            # lost_connection has no reading; the baseline is the honest stand-in.
            "hr_now": context.get("hr_now") or hr_baseline,
        },
        "tier": _TIER.get(context.get("urgency"), 1),
        "kin": [{"name": profile["kin_name"], "phone": profile["kin_phone"]}],
        # No support_contact: anything past kin is a human's job, so the
        # ladder ends after the kin rung (service-side skip when unset).
    }


def hand_off(user_id, profile, scenario, context, ladder_scenario=None,
             provider=None):
    """POST the alert; returns the service's ack {alert_id, state, status_url}.

    The ladder runs in the service's background — this returns immediately so
    the ingest path never blocks on phone calls. Raises on any HTTP failure
    (caller falls back to legacy telephony).

    ladder_scenario forces the dry-run provider's script over there (kin /
    support / resolved / unclear / unresolved) — the presenter console arms it
    so a live demo plays a chosen outcome deterministically. None = the
    service's default (and the live provider ignores it entirely).
    """
    payload = build_alert(user_id, profile, scenario, context)
    url = "%s/alerts" % escalation_url()
    params = {}
    if ladder_scenario:
        params["scenario"] = ladder_scenario
    if provider:
        # One-alert override of the service's CALL_PROVIDER: the demo runs
        # scripted dry runs all day and fires a single real call on demand.
        params["provider"] = provider
    resp = httpx.post(url, json=payload, params=params or None, timeout=5.0)
    resp.raise_for_status()
    return resp.json()
