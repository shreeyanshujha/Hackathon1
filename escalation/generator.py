"""Fake alerts matching the detection module's contract.

The detection module is not ready, so nothing here waits on it. These alerts
are the same shape it will hand over.
"""

from __future__ import annotations

import random

from .config import settings
from .machine import new_alert_id
from .models import Alert, Detail, Kin, SupportContact, UserProfile

SCENARIOS = [
    ("walking the dog", 58, 15, 88),
    ("making breakfast", 62, 12, 94),
    ("in the garden", 55, 22, 91),
    ("out for the morning walk", 60, 18, 97),
]


def make_alert(
    *,
    alert_id: str | None = None,
    name: str = "Jeff",
    tier: int = 3,
    randomise: bool = False,
) -> Alert:
    activity, baseline, stillness, hr_now = SCENARIOS[0]
    if randomise:
        activity, baseline, stillness, hr_now = random.choice(SCENARIOS)
        stillness += random.randint(-3, 6)
        hr_now += random.randint(-5, 9)

    return Alert(
        alert_id=alert_id or new_alert_id(),
        user=UserProfile(
            name=name,
            age=72,
            on_beta_blocker=True,
            hr_baseline=baseline,
            expected_activity=activity,
            phone=settings.jeff_phone,
        ),
        reason="still_with_rising_hr",
        detail=Detail(stillness_minutes=stillness, hr_now=hr_now),
        tier=tier,
        kin=[Kin(name="Jess", phone=settings.jess_phone)],
        support_contact=SupportContact(phone=settings.support_phone),
    )
