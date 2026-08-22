"""Anomaly rule engine + cooldown state machine.

Evaluates each telemetry reading against a discrepancy matrix:

  1. normal    - HR elevated + movement high          -> log only
  2. acute     - HR >= acute_hr_bpm sustained while
                 stationary/steps ~0                  -> voice escalation
  3. immobility- near-zero movement for a window
                 during waking hours                  -> voice escalation (low urgency)
  4. wandering - sustained movement inside the
                 profile's sleep window               -> voice escalation

Plus a missing-data watchdog (lost_connection) driven by main.py's
background task.

All thresholds come from config.yaml at evaluation time. Nothing numeric
is hardcoded here.
"""

import json
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent

# Scenarios that place a call, in priority order (first match wins per reading).
CALL_SCENARIOS = ("acute", "wandering", "immobility")

# Urgency carried on every TriggerEvent, keyed by scenario.
URGENCY = {
    "acute": "high",
    "wandering": "high",
    "immobility": "low",
    "lost_connection": "low",
}

MOVING_INTENSITIES = ("moderate", "high", "moving")


def load_config(path=None):
    with open(path or BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def load_profiles(path=None):
    with open(path or BASE_DIR / "profiles.json") as f:
        return json.load(f)


def parse_ts(value):
    """Parse an ISO timestamp into a naive local datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_time_window(dt, start_hhmm, end_hhmm):
    """True if dt's time-of-day falls in [start, end), handling midnight wrap."""
    t = dt.hour * 60 + dt.minute
    start, end = _parse_hhmm(start_hhmm), _parse_hhmm(end_hhmm)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def get_schedule_context(profile, now):
    """Frozen contract: clock + routine context for one user at one instant.

    Returns {"in_sleep_window": bool, "waking_hours": bool, "routine_note": str}.
    The booleans feed the discrepancy matrix; routine_note ("walking the dog",
    "asleep", "at home") rides on the TriggerEvent for the call script.
    """
    in_sleep = in_time_window(now, profile["sleep_window"]["start"],
                              profile["sleep_window"]["end"])
    routine_note = "asleep" if in_sleep else "at home"
    day = now.strftime("%A").lower()
    for entry in profile.get("routine", []):
        if entry["day"] == day and in_time_window(now, entry["start"], entry["end"]):
            routine_note = entry["activity"]
            break
    return {"in_sleep_window": in_sleep,
            "waking_hours": not in_sleep,
            "routine_note": routine_note}


class UserState:
    def __init__(self):
        self.readings = deque(maxlen=240)  # ~20 min at 5s cadence
        self.streaks = {}                  # scenario -> streak start (reading ts)
        self.cooldown_until = None         # wall-clock datetime
        self.last_seen = None              # wall-clock datetime (arrival time)
        self.last_reading_ts = None        # reading-supplied timestamp
        self.classification = "no data"
        self.status = "NORMAL"             # NORMAL | EVALUATING | ALERT
        self.active_scenario = None
        self.missing_flagged = False


class RuleEngine:
    def __init__(self, config_path=None, profiles_path=None, on_trigger=None):
        self._config_path = config_path
        self.profiles = load_profiles(profiles_path)
        self.on_trigger = on_trigger or (lambda *a, **k: None)
        self.users = {uid: UserState() for uid in self.profiles}
        self.events = deque(maxlen=300)
        self._lock = threading.Lock()

    # -- config is re-read on every evaluation so edits apply live ----------
    @property
    def thresholds(self):
        return load_config(self._config_path)["thresholds"]

    def log_event(self, user_id, etype, message, severity="info"):
        evt = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "user_id": user_id,
            "type": etype,
            "message": message,
            "severity": severity,
        }
        self.events.appendleft(evt)
        return evt

    # ------------------------------------------------------------------ ingest
    def ingest(self, reading):
        user_id = reading["user_id"]
        if user_id not in self.users:
            raise KeyError("unknown user_id %r" % user_id)

        with self._lock:
            state = self.users[user_id]
            ts = parse_ts(reading["timestamp"])
            state.readings.append({
                "ts": ts.isoformat(timespec="seconds"),
                "heart_rate_bpm": reading["heart_rate_bpm"],
                "step_count_last_5min": reading["step_count_last_5min"],
                "motion_intensity": reading["motion_intensity"],
                "battery_pct": reading.get("battery_pct"),
            })
            state.last_seen = datetime.now()
            state.last_reading_ts = ts
            if state.missing_flagged:
                state.missing_flagged = False
                self.log_event(user_id, "reconnected",
                               "Telemetry resumed from %s's watch" % self.profiles[user_id]["name"])
            return self._evaluate(user_id, state, reading, ts)

    # ---------------------------------------------------------------- evaluate
    def _evaluate(self, user_id, state, reading, ts):
        th = self.thresholds
        profile = self.profiles[user_id]

        hr = reading["heart_rate_bpm"]
        steps = reading["step_count_last_5min"]
        motion = reading["motion_intensity"]

        moving = motion in MOVING_INTENSITIES or steps > th["stationary_steps_max"]
        stationary = not moving
        hr_elevated = hr >= profile["resting_hr_bpm"] + th["elevated_hr_margin_bpm"]
        schedule_ctx = get_schedule_context(profile, ts)
        asleep_window = schedule_ctx["in_sleep_window"]

        conditions = {
            "acute": hr >= th["acute_hr_bpm"] and stationary,
            "wandering": moving and asleep_window,
            "immobility": stationary and not asleep_window,
        }
        windows = {
            "acute": th["acute_movement_window_sec"],
            "wandering": th["wandering_window_sec"],
            # Profiles may override the demo-compressed immobility window
            # (the live watch user would otherwise alert while sitting still).
            "immobility": profile.get("immobility_window_sec",
                                      th["immobility_window_sec"]),
        }

        triggered = None
        evaluating = []
        for scenario in CALL_SCENARIOS:
            if conditions[scenario]:
                streak = state.streaks.get(scenario)
                if streak is None:
                    streak = state.streaks[scenario] = {"start": ts, "fired": False}
                duration = (ts - streak["start"]).total_seconds()
                if duration >= windows[scenario]:
                    # Fire once per continuous streak; the cooldown machine
                    # guards across streaks.
                    if not streak["fired"] and triggered is None:
                        streak["fired"] = True
                        triggered = (scenario, duration)
                elif triggered is None:
                    evaluating.append((scenario, duration, windows[scenario]))
            else:
                state.streaks.pop(scenario, None)

        still_fired = [s for s in CALL_SCENARIOS
                       if state.streaks.get(s, {}).get("fired")]

        if triggered:
            scenario, duration = triggered
            state.classification = scenario
            self._trigger(user_id, state, scenario, {
                "hr": hr, "steps": steps, "motion": motion,
                "duration_sec": int(duration), "reading_ts": ts.isoformat(timespec="seconds"),
                "schedule_context": schedule_ctx,
            })
        elif still_fired:
            state.status = "ALERT"
            state.classification = "%s — incident active" % still_fired[0]
        elif evaluating:
            scenario, duration, window = evaluating[0]
            state.status = "EVALUATING"
            state.classification = "evaluating %s (%ds / %ds)" % (scenario, duration, window)
        elif hr_elevated and moving:
            state.status = "NORMAL"
            state.classification = "normal (elevated HR, active)"
            self.log_event(user_id, "normal_routine",
                           "%s: HR %d bpm while moving — consistent with activity, no action"
                           % (profile["name"], hr))
        else:
            state.status = "NORMAL"
            state.classification = "normal"

        return {"status": state.status, "classification": state.classification}

    # ----------------------------------------------------------------- trigger
    def _trigger(self, user_id, state, scenario, context):
        th = self.thresholds
        now = datetime.now()
        profile = self.profiles[user_id]

        if state.cooldown_until and now < state.cooldown_until:
            state.status = "ALERT"
            self.log_event(
                user_id, "suppressed",
                "%s condition still present for %s — call suppressed by cooldown (until %s)"
                % (scenario, profile["name"],
                   state.cooldown_until.strftime("%H:%M:%S")))
            return

        state.cooldown_until = now + timedelta(minutes=th["cooldown_min"])
        state.status = "ALERT"
        state.active_scenario = scenario

        # Frozen contract: every dispatch carries the full TriggerEvent shape
        # (scenario, urgency, hr_now, hr_baseline, duration_sec,
        # schedule_context, timestamp) alongside the raw reading fields.
        last = state.readings[-1] if state.readings else None
        gap_sec = int(context["gap_min"] * 60) if "gap_min" in context else None
        context.update({
            "scenario": scenario,
            "urgency": URGENCY.get(scenario, "high"),
            "hr_now": context.get("hr",
                                  last["heart_rate_bpm"] if last else None),
            "hr_baseline": profile["resting_hr_bpm"],
            "duration_sec": context.get("duration_sec", gap_sec),
            "schedule_context": context.get(
                "schedule_context", get_schedule_context(profile, now)),
            "timestamp": context.get(
                "reading_ts", now.isoformat(timespec="seconds")),
        })

        self.log_event(
            user_id, "alert",
            "ALERT [%s] for %s — HR %s, motion %s. Placing call to kin %s."
            % (scenario, profile["name"], context.get("hr", "n/a"),
               context.get("motion", "n/a"), profile["kin_name"]),
            severity="alert")
        self.on_trigger(user_id, scenario, context)

    # ---------------------------------------------------------------- watchdog
    def check_missing_data(self, now=None):
        """Called every 60s by main.py's asyncio task."""
        now = now or datetime.now()
        default_min = self.thresholds["missing_data_min"]
        with self._lock:
            for user_id, state in self.users.items():
                if state.last_seen is None or state.missing_flagged:
                    continue
                # Cloud-synced sources lag by design; profiles may override.
                limit = timedelta(minutes=self.profiles[user_id].get(
                    "missing_data_min", default_min))
                gap = now - state.last_seen
                if gap > limit:
                    state.missing_flagged = True
                    state.classification = "lost connection"
                    self._trigger(user_id, state, "lost_connection", {
                        "gap_min": int(gap.total_seconds() // 60),
                        "last_seen": state.last_seen.isoformat(timespec="seconds"),
                    })

    # ------------------------------------------------------------------- admin
    def reset_cooldown(self, user_id):
        state = self.users[user_id]
        state.cooldown_until = None
        state.status = "NORMAL"
        state.active_scenario = None
        state.streaks.clear()
        state.missing_flagged = False
        state.classification = "normal"
        self.log_event(user_id, "cooldown_reset", "Cooldown cleared for %s"
                       % self.profiles[user_id]["name"])

    def stand_down(self, user_id):
        state = self.users[user_id]
        state.status = "NORMAL"
        state.active_scenario = None
        self.log_event(user_id, "stand_down",
                       "Kin pressed 2 — stood down. Logged as false positive. "
                       "Cooldown remains active.", severity="info")

    def current_routine(self, user_id, dt=None):
        """Human phrase for what this user is normally doing right now."""
        return get_schedule_context(self.profiles[user_id],
                                    dt or datetime.now())["routine_note"]

    # -------------------------------------------------------------------- state
    def snapshot(self):
        now = datetime.now()
        users = {}
        with self._lock:
            for user_id, state in self.users.items():
                profile = self.profiles[user_id]
                latest = state.readings[-1] if state.readings else None
                cooldown_sec = 0
                if state.cooldown_until and state.cooldown_until > now:
                    cooldown_sec = int((state.cooldown_until - now).total_seconds())
                users[user_id] = {
                    "name": profile["name"],
                    "source": profile.get("source", "mock"),
                    "age": profile["age"],
                    "resting_hr_bpm": profile["resting_hr_bpm"],
                    "sleep_window": profile["sleep_window"],
                    "demo_scenario": profile.get("demo_scenario"),
                    "kin_name": profile["kin_name"],
                    "latest": latest,
                    "hr_history": [r["heart_rate_bpm"] for r in list(state.readings)[-60:]],
                    "status": state.status,
                    "classification": state.classification,
                    "active_scenario": state.active_scenario,
                    "cooldown_sec_remaining": cooldown_sec,
                    "last_seen_sec_ago": (
                        int((now - state.last_seen).total_seconds())
                        if state.last_seen else None),
                    "missing_flagged": state.missing_flagged,
                }
            return {
                "server_time": now.isoformat(timespec="seconds"),
                "users": users,
                "events": list(self.events)[:40],
            }
