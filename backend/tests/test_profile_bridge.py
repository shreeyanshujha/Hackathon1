"""Contract tests for profile_bridge: Module 1's UserBaselineProfile ->
engine profile fields.

The converter is pure (no I/O, `today` injected) and returns ONLY the fields
onboarding knows about; the caller merges them over the live card so
source/resting HR/threshold overrides survive.
"""

from datetime import date

import pytest

import profile_bridge as bridge

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday")
TODAY = date(2026, 8, 22)


def make_baseline(**over):
    baseline = {
        "schemaVersion": 1,
        "demographics": {"name": "Edna Krabappel", "sex": "female",
                         "dob": "1948-03-15", "livingSituation": "lives_alone"},
        "emergencyContacts": [
            {"name": "Sarah", "relationship": "Daughter",
             "phone": "+61400000001", "isPrimary": True},
            {"name": "Nurse Joy", "relationship": "Carer",
             "phone": "+61400000002", "isPrimary": False},
        ],
        "sleep": {"typicalWake": "06:45", "typicalSleep": "21:30",
                  "napPattern": None},
        "weeklyRoutine": {day: [] for day in DAYS},
        "hobbies": ["Gardening"],
        "mobilityLevel": "walking_aid",
        "lifestyle": {"diet": None,
                      "smoking": {"status": False, "frequency": None},
                      "alcohol": {"status": False, "frequency": None}},
        "healthContext": ["Arthritis"],
        "medicationCount": 2,
        "consent": {"monitoringConsent": True, "sharedWith": ["nextOfKin"]},
        "deviceId": None,
        "completedAt": "2026-08-22T10:00:00.000Z",
    }
    baseline.update(over)
    return baseline


def test_maps_core_fields():
    fields = bridge.baseline_to_profile(make_baseline(), today=TODAY)
    assert fields["name"] == "Edna Krabappel"
    assert fields["age"] == 78  # born 1948-03-15
    # Engine sleep_window: start = goes to sleep, end = wakes up.
    assert fields["sleep_window"] == {"start": "21:30", "end": "06:45"}
    assert fields["kin_name"] == "Sarah"
    assert fields["kin_phone"] == "+61400000001"
    assert fields["responder_phone"] == "+61400000002"


def test_no_responder_field_with_single_contact():
    baseline = make_baseline(emergencyContacts=[
        {"name": "Sarah", "relationship": "Daughter",
         "phone": "+61400000001", "isPrimary": True}])
    fields = bridge.baseline_to_profile(baseline, today=TODAY)
    # Absent (not None): the merge must keep the card's existing responder.
    assert "responder_phone" not in fields


def test_routine_flattened_to_engine_shape():
    routine = {day: [] for day in DAYS}
    routine["monday"] = [{"activity": "Morning walk",
                          "expectedTime": "10:00", "expectedDuration": 45}]
    routine["friday"] = [{"activity": "Grocery shopping",
                          "expectedTime": "14:00", "expectedDuration": None}]
    fields = bridge.baseline_to_profile(make_baseline(weeklyRoutine=routine),
                                        today=TODAY)
    assert {"day": "monday", "start": "10:00", "end": "10:45",
            "activity": "Morning walk"} in fields["routine"]
    # Missing duration defaults to an hour.
    assert {"day": "friday", "start": "14:00", "end": "15:00",
            "activity": "Grocery shopping"} in fields["routine"]


def test_routine_end_capped_before_midnight():
    # An entry running past midnight must not wrap (in_time_window would
    # read a wrapped range as covering most of the day).
    routine = {day: [] for day in DAYS}
    routine["saturday"] = [{"activity": "Late film",
                            "expectedTime": "23:30", "expectedDuration": 90}]
    fields = bridge.baseline_to_profile(make_baseline(weeklyRoutine=routine),
                                        today=TODAY)
    assert fields["routine"][0]["end"] == "23:59"


def test_rejects_missing_consent():
    baseline = make_baseline(
        consent={"monitoringConsent": False, "sharedWith": []})
    with pytest.raises(bridge.ConsentError):
        bridge.baseline_to_profile(baseline, today=TODAY)


def test_rejects_unknown_schema_version():
    with pytest.raises(bridge.BaselineError, match="schemaVersion"):
        bridge.baseline_to_profile(make_baseline(schemaVersion=2), today=TODAY)


def test_rejects_missing_primary_contact():
    baseline = make_baseline(emergencyContacts=[
        {"name": "Nurse Joy", "relationship": "Carer",
         "phone": "+61400000002", "isPrimary": False}])
    with pytest.raises(bridge.BaselineError, match="primary"):
        bridge.baseline_to_profile(baseline, today=TODAY)


def test_rejects_missing_sleep_baseline():
    baseline = make_baseline(sleep={"typicalWake": "", "typicalSleep": None,
                                    "napPattern": None})
    with pytest.raises(bridge.BaselineError, match="sleep"):
        bridge.baseline_to_profile(baseline, today=TODAY)


# --- phone numbers must reach the dialler in E.164 ------------------------
# Onboarding's phone field prompts for "0412 345 678", and the app stores
# exactly that (minus spacing). Twilio and ElevenLabs both reject anything
# that is not E.164, so a local-format number means the kin call fails at
# the moment it matters. The bridge is the last hop before the engine card.


def contacts(primary_phone, other_phone=None):
    people = [{"name": "Sarah", "relationship": "Daughter",
               "phone": primary_phone, "isPrimary": True}]
    if other_phone is not None:
        people.append({"name": "Nurse Joy", "relationship": "Carer",
                       "phone": other_phone, "isPrimary": False})
    return make_baseline(emergencyContacts=people)


def test_local_mobile_converted_to_e164():
    fields = bridge.baseline_to_profile(contacts("0412345678"), today=TODAY)
    assert fields["kin_phone"] == "+61412345678"


def test_local_landline_converted_to_e164():
    fields = bridge.baseline_to_profile(contacts("0298765432"), today=TODAY)
    assert fields["kin_phone"] == "+61298765432"


def test_spacing_and_punctuation_stripped():
    fields = bridge.baseline_to_profile(contacts("(04) 1234-5678"),
                                        today=TODAY)
    assert fields["kin_phone"] == "+61412345678"


def test_e164_number_passes_through_unchanged():
    fields = bridge.baseline_to_profile(contacts("+61400000001"), today=TODAY)
    assert fields["kin_phone"] == "+61400000001"


def test_responder_phone_normalised_too():
    fields = bridge.baseline_to_profile(contacts("0412345678", "0298765432"),
                                        today=TODAY)
    assert fields["responder_phone"] == "+61298765432"


def test_rejects_undiallable_primary_number():
    # Better to fail the sync loudly than to store a number that will only
    # reveal itself as undiallable during an emergency.
    with pytest.raises(bridge.BaselineError, match="phone"):
        bridge.baseline_to_profile(contacts("12345"), today=TODAY)
