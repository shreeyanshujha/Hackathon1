"""Tests for the telecare anomaly-detection engine (backend/engine.py).

Pure-logic tests: no network, no sleep() — time is simulated by passing
explicit `now` datetimes into the engine (a fake clock advanced by hand).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import (  # noqa: E402
    UserState,
    check_missing_data,
    get_status,
    process_reading,
    reset_cooldown,
)

# ---------------------------------------------------------------- fixtures

CONFIG = {
    "acute_hr_bpm": 120,
    "acute_movement_window_sec": 15,
    "immobility_window_sec": 30,
    "missing_data_min": 2,
    "cooldown_min": 60,
    "hysteresis_clear_sec": 5,
}

PROFILE = {"user_id": "usr_demo_a", "name": "Jeff", "age": 74, "resting_hr": 62}

BASE = datetime(2026, 8, 22, 10, 0, 0, tzinfo=timezone.utc)


def sched(sleep=False, waking=True, note="normally walking the dog at this time"):
    """Stub of the teammate's schedule-context resolver (C-IN-2)."""
    return {"in_sleep_window": sleep, "waking_hours": waking, "routine_note": note}


def make_reading(t_sec, hr, motion, steps=0, user_id="usr_demo_a"):
    """Synthetic C-IN-1 telemetry reading at BASE + t_sec."""
    ts = BASE + timedelta(seconds=t_sec)
    return {
        "user_id": user_id,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "heart_rate_bpm": hr,
        "step_count_last_5min": steps,
        "motion_intensity": motion,
        "battery_pct": 84,
    }


def run_sequence(state, seq, schedule_ctx, config=CONFIG, profile=PROFILE):
    """Feed a list of (t_sec, hr, motion[, steps]) tuples; return emitted events
    as (t_sec, event) pairs."""
    events = []
    for item in seq:
        t_sec, hr, motion = item[0], item[1], item[2]
        steps = item[3] if len(item) > 3 else 0
        now = BASE + timedelta(seconds=t_sec)
        evt = process_reading(
            state, make_reading(t_sec, hr, motion, steps),
            profile, schedule_ctx, config, now)
        if evt is not None:
            events.append((t_sec, evt))
    return events


def fresh_state():
    return UserState(CONFIG)


# ------------------------------------------------------------------- tests


def test_jumping_jacks_control_never_triggers():
    """Elevated HR while moving is normal exertion — the suppressor wins."""
    state = fresh_state()
    seq = [(t, 140, "moving") for t in range(0, 65, 5)]
    events = run_sequence(state, seq, sched())
    assert events == []
    assert get_status(state)["evaluation"] == "normal"


def test_acute_happy_path_fires_exactly_once():
    state = fresh_state()
    seq = [(t, 135, "stationary") for t in range(0, 35, 5)]
    events = run_sequence(state, seq, sched())
    assert len(events) == 1
    t_fired, evt = events[0]
    assert t_fired == 15  # first reading at/after the 15s window
    assert evt["scenario"] == "acute"
    assert evt["urgency"] == "high"
    assert evt["user_id"] == "usr_demo_a"
    assert evt["hr_now"] == 135
    assert evt["hr_baseline"] == 62
    assert evt["duration_sec"] == 15
    assert evt["motion"] == "stationary"
    assert evt["schedule_context"] == "normally walking the dog at this time"
    assert evt["timestamp"].endswith("Z")


def test_hysteresis_single_blip_does_not_reset_timer():
    """One hr=119-style dip mid-evaluation must not restart the acute timer."""
    state = fresh_state()
    seq = [
        (0, 135, "stationary"),
        (5, 135, "stationary"),
        (10, 115, "stationary"),  # the blip — condition momentarily clear
        (15, 135, "stationary"),  # resumes before hysteresis_clear_sec elapses
        (20, 135, "stationary"),
    ]
    events = run_sequence(state, seq, sched())
    assert len(events) == 1
    t_fired, evt = events[0]
    assert evt["scenario"] == "acute"
    # timer kept its original t=0 start, so duration reflects the full span
    assert evt["duration_sec"] == t_fired


def test_sustained_clear_does_reset_timer():
    """Condition clear for >= hysteresis_clear_sec continuously resets the timer."""
    state = fresh_state()
    seq = [
        (0, 135, "stationary"),
        (5, 135, "stationary"),
        (10, 90, "stationary"),   # clear...
        (16, 90, "stationary"),   # ...still clear 6s later -> reset
        (20, 135, "stationary"),  # new episode starts here
        (25, 135, "stationary"),
        (30, 135, "stationary"),
    ]
    events = run_sequence(state, seq, sched())
    # New episode's timer starts at t=20; 15s window not yet met by t=30.
    assert events == []


def test_cooldown_blocks_second_episode_until_reset():
    state = fresh_state()
    # Episode 1: fires at t=15.
    seq1 = [(t, 135, "stationary") for t in range(0, 25, 5)]
    events1 = run_sequence(state, seq1, sched())
    assert len(events1) == 1

    # Recovery: condition clear long enough to reset the timer.
    seq_calm = [(t, 70, "stationary") for t in range(30, 60, 5)]
    assert run_sequence(state, seq_calm, sched()) == []

    # Episode 2, five minutes later: qualifies again but cooldown suppresses it.
    seq2 = [(300 + t, 135, "stationary") for t in range(0, 25, 5)]
    events2 = run_sequence(state, seq2, sched())
    assert events2 == []
    # Rule still evaluates for the dashboard even in cooldown.
    assert get_status(state)["evaluation"] == "ALERT"

    # Operator stands down / clears cooldown -> next qualifying reading fires.
    reset_cooldown(state, "acute")
    now = BASE + timedelta(seconds=330)
    evt = process_reading(state, make_reading(330, 135, "stationary"),
                          PROFILE, sched(), CONFIG, now)
    assert evt is not None and evt["scenario"] == "acute"


def test_cooldown_is_scoped_per_scenario():
    """Acute in cooldown must not suppress a later lost_connection alert."""
    state = fresh_state()
    events = run_sequence(state, [(t, 135, "stationary") for t in range(0, 25, 5)],
                          sched())
    assert len(events) == 1  # acute fired -> acute now in cooldown

    # Telemetry stops after t=20; watchdog checks 3 minutes later.
    now = BASE + timedelta(seconds=20) + timedelta(minutes=3)
    evt = check_missing_data(state, PROFILE, CONFIG, now)
    assert evt is not None
    assert evt["scenario"] == "lost_connection"
    assert evt["urgency"] == "low"

    # But lost_connection itself is now in cooldown — no watchdog spam.
    assert check_missing_data(state, PROFILE, CONFIG,
                              now + timedelta(seconds=60)) is None


def test_missing_data_below_threshold_is_none():
    state = fresh_state()
    run_sequence(state, [(0, 70, "stationary")], sched())
    now = BASE + timedelta(seconds=60)  # only 1 min gap, threshold is 2 min
    assert check_missing_data(state, PROFILE, CONFIG, now) is None


def test_missing_data_with_no_readings_ever_is_none():
    state = fresh_state()
    assert check_missing_data(state, PROFILE, CONFIG, BASE) is None


def test_wandering_fires_only_in_sleep_window():
    seq = [(t, 70, "moving", 40) for t in range(0, 25, 5)]

    state = fresh_state()
    events = run_sequence(state, seq, sched(sleep=True, waking=False))
    assert len(events) == 1
    _, evt = events[0]
    assert evt["scenario"] == "wandering"
    assert evt["urgency"] == "med"
    assert evt["motion"] == "moving"

    # Identical telemetry outside the sleep window: nothing.
    state2 = fresh_state()
    assert run_sequence(state2, seq, sched(sleep=False, waking=True)) == []


def test_immobility_fires_only_during_waking_hours():
    seq = [(t, 70, "stationary", 0) for t in range(0, 40, 5)]

    state = fresh_state()
    events = run_sequence(state, seq, sched(sleep=False, waking=True))
    assert len(events) == 1
    t_fired, evt = events[0]
    assert t_fired == 30
    assert evt["scenario"] == "immobility"
    assert evt["urgency"] == "med"
    assert evt["duration_sec"] == 30

    # Same telemetry while asleep: stillness is expected, no alert.
    state2 = fresh_state()
    assert run_sequence(state2, seq, sched(sleep=True, waking=False)) == []


def test_immobility_yields_to_acute_when_hr_elevated():
    """Elevated HR while stationary is acute's job, never immobility's."""
    state = fresh_state()
    seq = [(t, 135, "stationary") for t in range(0, 40, 5)]
    events = run_sequence(state, seq, sched())
    assert len(events) == 1
    assert events[0][1]["scenario"] == "acute"


def test_sensor_dropout_does_not_crash_and_motion_rules_still_work():
    state = fresh_state()
    # hr=None interleaved throughout a stationary immobility build-up.
    seq = [(t, None if (t // 5) % 2 == 0 else 70, "stationary", 0)
           for t in range(0, 40, 5)]
    events = run_sequence(state, seq, sched(waking=True))
    assert len(events) == 1
    _, evt = events[0]
    assert evt["scenario"] == "immobility"

    # hr=None must never be treated as hr=0 or elevated: no acute involvement.
    state2 = fresh_state()
    seq2 = [(t, None, "stationary") for t in range(0, 25, 5)]
    events2 = run_sequence(state2, seq2, sched(waking=False, sleep=False))
    assert events2 == []


def test_status_transitions_normal_evaluating_alert():
    state = fresh_state()
    assert get_status(state)["evaluation"] == "normal"

    now0 = BASE
    process_reading(state, make_reading(0, 135, "stationary"),
                    PROFILE, sched(), CONFIG, now0)
    process_reading(state, make_reading(5, 135, "stationary"),
                    PROFILE, sched(), CONFIG, BASE + timedelta(seconds=5))
    st = get_status(state)
    assert st["evaluation"] == "evaluating"
    assert st["active_scenario"] is None

    for t in (10, 15):
        evt = process_reading(state, make_reading(t, 135, "stationary"),
                              PROFILE, sched(), CONFIG,
                              BASE + timedelta(seconds=t))
    assert evt is not None  # fired on the t=15 reading
    st = get_status(state)
    assert st["evaluation"] == "ALERT"
    assert st["active_scenario"] == "acute"
    assert "acute" in st["cooldown_until"]


def test_get_status_snapshot_fields():
    state = fresh_state()
    st = get_status(state)
    assert st["last_seen"] is None
    assert st["hr"] is None
    assert st["motion"] is None
    assert st["hr_history"] == []
    assert st["cooldown_until"] == {}

    run_sequence(state, [(0, 80, "moving", 12), (5, 82, "moving", 20)], sched())
    st = get_status(state)
    assert st["last_seen"] == (BASE + timedelta(seconds=5)).isoformat().replace(
        "+00:00", "Z")
    assert st["hr"] == 82
    assert st["motion"] == "moving"
    assert st["hr_history"] == [80, 82]


def test_hr_history_caps_at_60_values():
    state = fresh_state()
    seq = [(t, 60 + (t // 5) % 30, "moving", 10) for t in range(0, 5 * 70, 5)]
    run_sequence(state, seq, sched())
    history = get_status(state)["hr_history"]
    assert len(history) == 60
    # oldest first: the final reading's hr is the last element
    assert history[-1] == 60 + 69 % 30


def test_reset_cooldown_all_scenarios():
    state = fresh_state()
    run_sequence(state, [(t, 135, "stationary") for t in range(0, 25, 5)], sched())
    now = BASE + timedelta(seconds=20) + timedelta(minutes=3)
    check_missing_data(state, PROFILE, CONFIG, now)
    assert len(get_status(state)["cooldown_until"]) == 2
    reset_cooldown(state)  # no scenario -> clear everything
    assert get_status(state)["cooldown_until"] == {}
