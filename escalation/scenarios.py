"""Canned call scripts for the demo.

Shared by `python -m escalation.demo` and `POST /trigger?scenario=...` so both
show the same paths. Only used by the dry-run provider; with
CALL_PROVIDER=elevenlabs the phone decides what happens.
"""

from __future__ import annotations

from enum import Enum

from .config import settings
from .models import RelayOutcome, UserOutcome
from .providers.dryrun import ScriptedCall


class ScenarioName(str, Enum):
    KIN = "kin"
    SUPPORT = "support"
    RESOLVED = "resolved"
    UNCLEAR = "unclear"
    UNRESOLVED = "unresolved"


BLURBS = {
    ScenarioName.KIN: "No answer from Jeff, Jess asks for an ambulance",
    ScenarioName.SUPPORT: "Nobody answers from Jeff through kin, support decides",
    ScenarioName.RESOLVED: "Jeff picks up and answers the grounding question",
    ScenarioName.UNCLEAR: "Jeff answers but is confused, Jess declines",
    ScenarioName.UNRESOLVED: "Nobody answers anywhere, the chain is exhausted",
}


def script_for(name: ScenarioName) -> dict[str, ScriptedCall]:
    """Built fresh each time so it picks up the current demo phone numbers."""
    jeff = settings.jeff_phone
    jess = settings.jess_phone
    support = settings.support_phone

    if name is ScenarioName.RESOLVED:
        return {
            jeff: ScriptedCall(
                answered=True,
                outcome=UserOutcome.RESOLVED_OK.value,
                transcript="Oh, hello. I'm alright, just sat down with a cup of tea "
                           "after taking Rusty round the park.",
                confidence=0.93,
                grounded=True,
            )
        }

    if name is ScenarioName.KIN:
        return {
            jeff: ScriptedCall(answered=False),
            jess: ScriptedCall(
                answered=True,
                outcome=RelayOutcome.AMBULANCE_REQUESTED.value,
                transcript="That's not like him at all. Yes, please send an ambulance.",
                confidence=0.96,
            ),
        }

    if name is ScenarioName.SUPPORT:
        return {
            jeff: ScriptedCall(answered=False),
            jess: ScriptedCall(answered=False),
            support: ScriptedCall(
                answered=True,
                outcome=RelayOutcome.AMBULANCE_REQUESTED.value,
                transcript="Understood. Yes, dispatch an ambulance to his address.",
                confidence=0.97,
            ),
        }

    if name is ScenarioName.UNCLEAR:
        return {
            jeff: ScriptedCall(
                answered=True,
                outcome=UserOutcome.UNCLEAR.value,
                transcript="Who... the dog is... where's the... hello?",
                confidence=0.9,
                grounded=False,
            ),
            jess: ScriptedCall(
                answered=True,
                outcome=RelayOutcome.DECLINED.value,
                transcript="No, I'm sat right next to him, he's just having a doze.",
                confidence=0.94,
            ),
        }

    return {}  # UNRESOLVED: nobody answers
