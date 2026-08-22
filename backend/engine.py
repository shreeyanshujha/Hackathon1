"""Telecare anomaly-detection core: pure logic, zero I/O.

Decides, per incoming Fitbit reading, whether the wearer is in distress and
emits a structured TriggerEvent that downstream code turns into phone calls.
Four rules plus one suppressor, in plain English:

* Suppressor — normal exertion. If heart rate is elevated but the wearer is
  MOVING, that's exercise, not an emergency (jumping jacks, dog walks). All
  distress timers reset and nothing fires. This is what makes the engine
  context-aware rather than a dumb HR threshold.
* Acute (urgency: high) — heart rate at or above the acute threshold while
  the wearer is STATIONARY, both sustained for the acute window. High HR with
  no movement to explain it suggests a cardiac event or panic.
* Wandering (urgency: med) — sustained movement during the wearer's sleep
  window. An elderly person up and moving at 3am may be disoriented.
* Immobility (urgency: med) — stationary with near-zero steps for the
  immobility window during waking hours, with heart rate NOT elevated
  (elevated + stationary is acute's job). Suggests a fall or collapse.
* Lost connection (urgency: low) — the watchdog (`check_missing_data`) fires
  when no telemetry has arrived for longer than the missing-data threshold.

Robustness features:
* Hysteresis — a single anomalous reading (e.g. one hr=119 dip mid-episode)
  does not reset a building condition timer; the condition must be clear
  continuously for `hysteresis_clear_sec` before its timer resets.
* Per-scenario cooldown — after a scenario fires it cannot re-fire until its
  cooldown expires, but other scenarios are unaffected (a stood-down acute
  alert never masks a later lost_connection).
* Sensor dropout — a missing/None heart rate skips HR-based rules for that
  reading (never treated as hr=0); motion-based rules keep working.

All state lives in UserState instances (the wrapper holds one per user_id);
all thresholds come from the config dict at evaluation time; `now` is always
passed in, never read from the wall clock — which is what makes this module
deterministic and testable.
"""

import json
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent

# Structural sizes (not tunable rule thresholds — those live in config).
RING_BUFFER_SEC = 300   # keep the last 5 minutes of readings
HR_HISTORY_LEN = 60     # dashboard sparkline length

URGENCY = {
    "acute": "high",
    "immobility": "med",
    "wandering": "med",
    "lost_connection": "low",
}

# Rule evaluation order per reading — first match wins.
SCENARIO_ORDER = ("acute", "wandering", "immobility")


def scenario_windows(config: dict) -> dict:
    """Sustained-condition window (seconds) per scenario."""
    return {
        "acute": config["acute_movement_window_sec"],
        "wandering": config.get("wandering_window_sec")
        or config["acute_movement_window_sec"],
        "immobility": config["immobility_window_sec"],
    }


def _iso(dt: Optional[datetime]) -> Optional[str]:
    """ISO-8601 with a trailing Z, matching the telemetry contract."""
    if dt is None:
        return None
    s = dt.isoformat()
    if s.endswith("+00:00"):
        return s[: -len("+00:00")] + "Z"
    if dt.tzinfo is None:
        return s + "Z"
    return s


class UserState:
    """All per-user mutable state. Wrapper holds one per user_id."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config
        # Ring buffer: last RING_BUFFER_SEC of readings, oldest first.
        # Entries: {"ts": datetime, "hr": int|None, "steps": int|None,
        #           "motion": str}
        self.readings: deque = deque()
        self.hr_history: deque = deque(maxlen=HR_HISTORY_LEN)
        # Condition timers: scenario -> datetime the condition began.
        self.condition_start: dict = {}
        # Hysteresis: scenario -> datetime the condition was first seen clear
        # (while its timer is still running).
        self.condition_clear_since: dict = {}
        # Cooldown is per scenario: scenario -> expiry datetime.
        self.cooldown_until: dict = {}
        self.last_seen: Optional[datetime] = None
        self.last_hr: Optional[int] = None
        self.last_motion: Optional[str] = None
        # Dashboard state machine: "normal" | "evaluating" | "ALERT"
        self.evaluation: str = "normal"
        self.active_scenario: Optional[str] = None
        # Wrapper-managed presentation/watchdog fields (unused by the pure
        # core, kept here so RuleEngine and tests share one state class).
        self.classification: str = "no data"
        self.latest: Optional[dict] = None
        self.missing_flagged: bool = False


def _update_condition_timer(state: UserState, scenario: str, is_true: bool,
                            now: datetime, config: dict) -> None:
    """Advance one condition's timer with hysteresis.

    True -> (re)start the timer if not running, cancel any pending clear.
    False -> don't reset immediately; only reset once the condition has been
    clear continuously for >= hysteresis_clear_sec.
    """
    if is_true:
        state.condition_start.setdefault(scenario, now)
        state.condition_clear_since.pop(scenario, None)
    elif scenario in state.condition_start:
        clear_since = state.condition_clear_since.setdefault(scenario, now)
        clear_sec = (now - clear_since).total_seconds()
        if clear_sec >= config["hysteresis_clear_sec"]:
            del state.condition_start[scenario]
            del state.condition_clear_since[scenario]


def _clear_condition(state: UserState, scenario: str) -> None:
    """Hard reset (no hysteresis) — used by the suppressor."""
    state.condition_start.pop(scenario, None)
    state.condition_clear_since.pop(scenario, None)


def _in_cooldown(state: UserState, scenario: str, now: datetime) -> bool:
    expiry = state.cooldown_until.get(scenario)
    return expiry is not None and now < expiry


def _make_event(state: UserState, profile: dict, scenario: str,
                duration_sec: float, schedule_context: Optional[str],
                now: datetime) -> dict:
    return {
        "user_id": profile["user_id"],
        "scenario": scenario,
        "urgency": URGENCY[scenario],
        "hr_now": state.last_hr,
        "hr_baseline": profile.get("resting_hr"),
        "duration_sec": int(duration_sec),
        "motion": state.last_motion,
        "schedule_context": schedule_context,
        "timestamp": _iso(now),
    }


def process_reading(state: UserState, reading: dict, profile: dict,
                    schedule_ctx: dict, config: dict,
                    now: datetime) -> Optional[dict]:
    """Evaluate one reading. Returns a TriggerEvent dict or None.

    `now` is ALWAYS passed in — never call datetime.now() anywhere in this
    module. This is what makes the engine testable.
    """
    hr = reading.get("heart_rate_bpm")
    steps = reading.get("step_count_last_5min")
    motion = reading.get("motion_intensity")

    # -- ingest: ring buffer, sparkline, liveness -------------------------
    state.readings.append({"ts": now, "hr": hr, "steps": steps,
                           "motion": motion})
    while state.readings and \
            (now - state.readings[0]["ts"]).total_seconds() > RING_BUFFER_SEC:
        state.readings.popleft()
    state.hr_history.append(hr)
    state.last_seen = now
    state.last_hr = hr
    state.last_motion = motion

    hr_known = hr is not None
    hr_elevated = hr_known and hr >= config["acute_hr_bpm"]
    stationary = motion == "stationary"
    moving = motion == "moving"
    steps_near_zero = steps is None or \
        steps <= config.get("immobility_steps_max", 0)

    # -- suppressor (short-circuits everything) ---------------------------
    # Elevated HR while moving = normal exertion, not distress.
    if hr_elevated and moving:
        _clear_condition(state, "acute")
        _clear_condition(state, "immobility")
        state.evaluation = "normal"
        state.active_scenario = None
        return None

    conditions = {
        "acute": hr_known and hr_elevated and stationary,
        "wandering": moving and bool(schedule_ctx.get("in_sleep_window")),
        "immobility": stationary and steps_near_zero and not hr_elevated
        and bool(schedule_ctx.get("waking_hours")),
    }
    windows = scenario_windows(config)

    # -- advance condition timers (hysteresis-protected) ------------------
    # Sensor dropout: with hr unknown, the acute condition is unknowable, so
    # its timer is frozen for this reading (neither started nor cleared).
    for scenario, is_true in conditions.items():
        if scenario == "acute" and not hr_known:
            continue
        _update_condition_timer(state, scenario, is_true, now, config)

    # -- fire / dashboard state, first match wins -------------------------
    event = None
    alert_scenario = None
    evaluating = False
    # A condition counts toward firing only if it is true RIGHT NOW (its
    # timer may survive a blip via hysteresis, but a blip reading can't fire).
    for scenario in SCENARIO_ORDER:
        start = state.condition_start.get(scenario)
        if start is None or not conditions[scenario]:
            continue
        duration = (now - start).total_seconds()
        if duration >= windows[scenario]:
            if alert_scenario is None:
                alert_scenario = scenario
            # In cooldown the rule still evaluates (dashboard shows ALERT)
            # but cannot emit again.
            if event is None and not _in_cooldown(state, scenario, now):
                state.cooldown_until[scenario] = \
                    now + timedelta(minutes=config["cooldown_min"])
                event = _make_event(state, profile, scenario, duration,
                                    schedule_ctx.get("routine_note"), now)
        else:
            evaluating = True

    if alert_scenario is not None:
        state.evaluation = "ALERT"
        state.active_scenario = alert_scenario
    elif evaluating:
        state.evaluation = "evaluating"
        state.active_scenario = None
    else:
        state.evaluation = "normal"
        state.active_scenario = None

    return event


def check_missing_data(state: UserState, profile: dict, config: dict,
                       now: datetime) -> Optional[dict]:
    """Called periodically by the wrapper's watchdog. Returns a
    lost_connection TriggerEvent if now - last_seen exceeds missing_data_min,
    else None."""
    if state.last_seen is None:
        return None
    gap_sec = (now - state.last_seen).total_seconds()
    if gap_sec <= config["missing_data_min"] * 60:
        return None
    if _in_cooldown(state, "lost_connection", now):
        return None
    state.cooldown_until["lost_connection"] = \
        now + timedelta(minutes=config["cooldown_min"])
    state.evaluation = "ALERT"
    state.active_scenario = "lost_connection"
    return _make_event(state, profile, "lost_connection", gap_sec, None, now)


def reset_cooldown(state: UserState, scenario: Optional[str] = None) -> None:
    """Clear cooldown for one scenario, or all if scenario is None."""
    if scenario is None:
        state.cooldown_until.clear()
    else:
        state.cooldown_until.pop(scenario, None)


def get_status(state: UserState) -> dict:
    """Dashboard snapshot for one user."""
    return {
        "evaluation": state.evaluation,
        "active_scenario": state.active_scenario,
        "cooldown_until": {s: _iso(dt)
                           for s, dt in state.cooldown_until.items()},
        "last_seen": _iso(state.last_seen),
        "hr": state.last_hr,
        "motion": state.last_motion,
        "hr_history": list(state.hr_history),
    }


# ===========================================================================
# Server-side wrapper: config/profile loading, event log, dashboard snapshot.
# main.py talks ONLY to RuleEngine; RuleEngine delegates every decision to the
# pure core above (process_reading / check_missing_data).
# ===========================================================================

# Raw motion_intensity values (watch + simulator) that count as movement.
MOVING_INTENSITIES = ("moderate", "high", "moving")

_STATUS = {"normal": "NORMAL", "evaluating": "EVALUATING", "ALERT": "ALERT"}


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
    """True if dt's time-of-day falls in [start, end], handling midnight wrap."""
    t = dt.hour * 60 + dt.minute
    start, end = _parse_hhmm(start_hhmm), _parse_hhmm(end_hhmm)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


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

    # ------------------------------------------------------------- adapters
    def _core_profile(self, user_id):
        profile = self.profiles[user_id]
        return {**profile, "user_id": user_id,
                "resting_hr": profile["resting_hr_bpm"]}

    def _schedule_ctx(self, user_id, dt):
        profile = self.profiles[user_id]
        asleep = in_time_window(dt, profile["sleep_window"]["start"],
                                profile["sleep_window"]["end"])
        return {"in_sleep_window": asleep, "waking_hours": not asleep,
                "routine_note": self.current_routine(user_id, dt)}

    # ------------------------------------------------------------------ ingest
    def ingest(self, reading):
        user_id = reading["user_id"]
        if user_id not in self.users:
            raise KeyError("unknown user_id %r" % user_id)

        th = self.thresholds
        with self._lock:
            state = self.users[user_id]
            profile = self.profiles[user_id]
            ts = parse_ts(reading["timestamp"])

            hr = reading.get("heart_rate_bpm")
            steps = reading.get("step_count_last_5min") or 0
            motion_raw = reading.get("motion_intensity")
            moving = motion_raw in MOVING_INTENSITIES or \
                steps > th["stationary_steps_max"]

            state.latest = {
                "ts": ts.isoformat(timespec="seconds"),
                "heart_rate_bpm": hr,
                "step_count_last_5min": reading.get("step_count_last_5min"),
                "motion_intensity": motion_raw,
                "battery_pct": reading.get("battery_pct"),
            }
            if state.missing_flagged:
                state.missing_flagged = False
                self.log_event(user_id, "reconnected",
                               "Telemetry resumed from %s's watch"
                               % profile["name"])

            prev_eval = state.evaluation
            event = process_reading(
                state,
                {"heart_rate_bpm": hr,
                 "step_count_last_5min": reading.get("step_count_last_5min"),
                 "motion_intensity": "moving" if moving else "stationary"},
                self._core_profile(user_id),
                self._schedule_ctx(user_id, ts),
                th, ts)

            if event is not None:
                scenario = event["scenario"]
                state.classification = scenario
                context = {
                    "hr": hr, "steps": steps, "motion": motion_raw,
                    "duration_sec": event["duration_sec"],
                    "reading_ts": ts.isoformat(timespec="seconds"),
                }
                self.log_event(
                    user_id, "alert",
                    "ALERT [%s] for %s — HR %s, motion %s. Placing call to "
                    "kin %s." % (scenario, profile["name"], hr, motion_raw,
                                 profile["kin_name"]),
                    severity="alert")
                self.on_trigger(user_id, scenario, context)
            elif state.evaluation == "ALERT":
                scenario = state.active_scenario
                state.classification = "%s — incident active" % scenario
                if prev_eval != "ALERT":
                    expiry = state.cooldown_until.get(scenario)
                    self.log_event(
                        user_id, "suppressed",
                        "%s condition still present for %s — call suppressed "
                        "by cooldown (until %s)"
                        % (scenario, profile["name"],
                           expiry.strftime("%H:%M:%S") if expiry else "n/a"))
            elif state.evaluation == "evaluating":
                windows = scenario_windows(th)
                state.classification = "evaluating"
                for scenario in SCENARIO_ORDER:
                    start = state.condition_start.get(scenario)
                    if start is None:
                        continue
                    duration = (ts - start).total_seconds()
                    if duration < windows[scenario]:
                        state.classification = "evaluating %s (%ds / %ds)" % (
                            scenario, duration, windows[scenario])
                        break
            elif moving and hr is not None and \
                    hr >= profile["resting_hr_bpm"] + th["elevated_hr_margin_bpm"]:
                state.classification = "normal (elevated HR, active)"
                self.log_event(
                    user_id, "normal_routine",
                    "%s: HR %d bpm while moving — consistent with activity, "
                    "no action" % (profile["name"], hr))
            else:
                state.classification = "normal"

            return {"status": _STATUS[state.evaluation],
                    "classification": state.classification}

    # ---------------------------------------------------------------- watchdog
    def check_missing_data(self, now=None):
        """Called every 60s by main.py's asyncio task."""
        now = now or datetime.now()
        th = self.thresholds
        with self._lock:
            for user_id, state in self.users.items():
                if state.missing_flagged:
                    continue
                event = check_missing_data(state, self._core_profile(user_id),
                                           th, now)
                if event is None:
                    continue
                profile = self.profiles[user_id]
                state.missing_flagged = True
                state.classification = "lost connection"
                gap_min = int(event["duration_sec"] // 60)
                self.log_event(
                    user_id, "alert",
                    "ALERT [lost_connection] for %s — no telemetry for ~%d "
                    "min. Placing call to kin %s."
                    % (profile["name"], gap_min, profile["kin_name"]),
                    severity="alert")
                self.on_trigger(user_id, "lost_connection", {
                    "gap_min": gap_min,
                    "last_seen": _iso(state.last_seen),
                })

    # ------------------------------------------------------------------- admin
    def reset_cooldown(self, user_id):
        state = self.users[user_id]
        state.cooldown_until.clear()
        state.condition_start.clear()
        state.condition_clear_since.clear()
        state.evaluation = "normal"
        state.active_scenario = None
        state.missing_flagged = False
        state.classification = "normal"
        self.log_event(user_id, "cooldown_reset", "Cooldown cleared for %s"
                       % self.profiles[user_id]["name"])

    def stand_down(self, user_id):
        state = self.users[user_id]
        state.evaluation = "normal"
        state.active_scenario = None
        state.classification = "normal"
        self.log_event(user_id, "stand_down",
                       "Kin pressed 2 — stood down. Logged as false positive. "
                       "Cooldown remains active.", severity="info")

    def current_routine(self, user_id, dt=None):
        """Human sentence for what this user is normally doing right now."""
        dt = dt or datetime.now()
        profile = self.profiles[user_id]
        day = dt.strftime("%A").lower()
        for entry in profile.get("routine", []):
            if entry["day"] == day and in_time_window(dt, entry["start"],
                                                      entry["end"]):
                return entry["activity"]
        if in_time_window(dt, profile["sleep_window"]["start"],
                          profile["sleep_window"]["end"]):
            return "asleep"
        return "at home"

    # -------------------------------------------------------------------- state
    def snapshot(self):
        now = datetime.now()
        users = {}
        with self._lock:
            for user_id, state in self.users.items():
                profile = self.profiles[user_id]
                cooldown_sec = 0
                for expiry in state.cooldown_until.values():
                    if expiry > now:
                        cooldown_sec = max(
                            cooldown_sec, int((expiry - now).total_seconds()))
                users[user_id] = {
                    "name": profile["name"],
                    "age": profile["age"],
                    "resting_hr_bpm": profile["resting_hr_bpm"],
                    "sleep_window": profile["sleep_window"],
                    "demo_scenario": profile.get("demo_scenario"),
                    "kin_name": profile["kin_name"],
                    "latest": state.latest,
                    "hr_history": list(state.hr_history),
                    "status": _STATUS[state.evaluation],
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
