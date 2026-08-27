"""Module 1 -> Module 2 bridge: UserBaselineProfile to engine profile fields.

Pure conversion, no I/O. Returns ONLY the fields onboarding knows about
(name, age, sleep_window, routine, kin, responder); the caller merges them
over the live card so everything onboarding can't know — source,
resting_hr_bpm (Fitbit auto-calibrates it), per-profile threshold
overrides — survives the sync.

Consent is a hard gate: a profile without monitoringConsent is refused, not
quietly accepted. That is the product promise Module 1 makes on its consent
screen.
"""

import re
from datetime import date, datetime

SCHEMA_VERSION = 1
DEFAULT_ROUTINE_DURATION_MIN = 60

# Every contact number is stored in E.164, because that is the only form
# Twilio and ElevenLabs will dial. Onboarding prompts for the local form
# ("0412 345 678"), so the trunk 0 is swapped for the country code here --
# the last hop before the number lands on the engine card.
DEFAULT_COUNTRY_CODE = "+61"          # the fleet is Australian
_PHONE_SEPARATORS = re.compile(r"[\s().-]")

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday")


class BaselineError(ValueError):
    """The baseline profile is malformed or incomplete."""


class ConsentError(BaselineError):
    """Monitoring consent has not been given."""


def _age(dob_iso, today):
    born = date.fromisoformat(dob_iso)
    age = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        age -= 1
    return age


def _e164(raw, field):
    """Local or international contact number -> E.164, or raise.

    Refusing is deliberate: an undiallable number that survives the sync
    only reveals itself when the ladder tries to place the call, which is
    the one moment there is no time to fix it.
    """
    digits = _PHONE_SEPARATORS.sub("", str(raw or ""))
    if digits.startswith("+"):
        national = digits[1:]
    elif digits.startswith("0"):        # local trunk prefix
        national = DEFAULT_COUNTRY_CODE[1:] + digits[1:]
    else:
        national = ""
    if not national.isdigit() or not 8 <= len(national) <= 15:
        raise BaselineError(
            "%s %r is not a diallable phone number (expected 0412 345 678 "
            "or +61412345678)" % (field, raw))
    return "+" + national


def _end_of_entry(start_hhmm, duration_min):
    """Entry end time, capped at 23:59 so a routine never wraps midnight
    (in_time_window reads start > end as a wrapped, near-all-day range)."""
    h, m = (int(x) for x in start_hhmm.split(":"))
    total = min(h * 60 + m + duration_min, 23 * 60 + 59)
    return "%02d:%02d" % divmod(total, 60)


def baseline_to_profile(baseline, today=None):
    """Convert a UserBaselineProfile dict to engine profile fields.

    Raises ConsentError without monitoring consent, BaselineError on any
    other contract violation.
    """
    today = today or date.today()

    if baseline.get("schemaVersion") != SCHEMA_VERSION:
        raise BaselineError("unsupported schemaVersion %r (expected %d)"
                            % (baseline.get("schemaVersion"), SCHEMA_VERSION))
    if not (baseline.get("consent") or {}).get("monitoringConsent"):
        raise ConsentError("monitoring consent has not been given")

    demographics = baseline.get("demographics") or {}
    if not demographics.get("name") or not demographics.get("dob"):
        raise BaselineError("demographics must include name and dob")
    try:
        age = _age(demographics["dob"], today)
    except ValueError:
        raise BaselineError("invalid dob %r (expected YYYY-MM-DD)"
                            % demographics["dob"])

    contacts = baseline.get("emergencyContacts") or []
    primaries = [c for c in contacts if c.get("isPrimary")]
    if len(primaries) != 1 or not primaries[0].get("phone"):
        raise BaselineError(
            "exactly one primary emergency contact with a phone is required")
    primary = primaries[0]
    others = [c for c in contacts if not c.get("isPrimary") and c.get("phone")]

    sleep = baseline.get("sleep") or {}
    if not sleep.get("typicalSleep") or not sleep.get("typicalWake"):
        raise BaselineError("sleep baseline (typicalSleep/typicalWake) is "
                            "required")

    routine = []
    weekly = baseline.get("weeklyRoutine") or {}
    for day in DAYS:
        for entry in weekly.get(day) or []:
            duration = entry.get("expectedDuration") or \
                DEFAULT_ROUTINE_DURATION_MIN
            routine.append({
                "day": day,
                "start": entry["expectedTime"],
                "end": _end_of_entry(entry["expectedTime"], duration),
                "activity": entry["activity"],
            })

    fields = {
        "name": demographics["name"],
        "age": age,
        "sleep_window": {"start": sleep["typicalSleep"],
                         "end": sleep["typicalWake"]},
        "routine": routine,
        "kin_name": primary["name"],
        "kin_phone": _e164(primary["phone"], "primary contact phone"),
        "baseline_synced_at": datetime.now().isoformat(timespec="seconds"),
    }
    if others:
        fields["responder_phone"] = _e164(others[0]["phone"],
                                          "responder phone")
    return fields
