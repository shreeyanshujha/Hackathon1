"""The {{slot}} contract between the prompts, render(), and ElevenLabs.

The three system prompts are built at import time by substituting the product
name into a template that also carries {{slots}}. Those slots must survive that
substitution untouched: render() fills them by literal "{{name}}" match, and
ElevenLabs' dynamic_variables expects the same double-brace form. A templating
step that collapses {{name}} to {name} breaks both paths silently — the call
still goes out, just with "{user_name}" read aloud instead of "Jeff".
"""

import re

import pytest

from escalation.config import PRODUCT_NAME
from escalation.machine import _kin_context, _support_context, _user_context
from escalation.prompts import (
    AGENT_A_SYSTEM,
    AGENT_B_SYSTEM,
    AGENT_SUPPORT_SYSTEM,
    DRY_RUN_OPENINGS,
    render,
)

SLOT = re.compile(r"\{\{(\w+)\}\}")

# Slots each prompt must still be carrying once the module has been imported.
EXPECTED_SLOTS = {
    "AGENT_A_SYSTEM": (
        AGENT_A_SYSTEM,
        {"user_name", "stillness_minutes", "hr_baseline", "hr_now"},
    ),
    "AGENT_B_SYSTEM": (
        AGENT_B_SYSTEM,
        {"kin_name", "user_name", "stillness_minutes", "expected_activity", "agent_a_outcome"},
    ),
    "AGENT_SUPPORT_SYSTEM": (
        AGENT_SUPPORT_SYSTEM,
        {"user_name", "stillness_minutes", "expected_activity", "agent_a_outcome"},
    ),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SLOTS))
def test_double_brace_slots_survive_import(name):
    prompt, expected = EXPECTED_SLOTS[name]
    for slot in sorted(expected):
        assert "{{" + slot + "}}" in prompt, f"{name} lost the {{{{{slot}}}}} slot"


@pytest.mark.parametrize("name", sorted(EXPECTED_SLOTS))
def test_slots_were_not_collapsed_to_single_braces(name):
    """The .format() failure mode: {{user_name}} arriving as {user_name}."""
    prompt, expected = EXPECTED_SLOTS[name]
    singles = set(re.findall(r"(?<!\{)\{(\w+)\}(?!\})", prompt))
    assert not singles & expected, (
        f"{name} has collapsed slots {sorted(singles & expected)} — render() "
        "matches on double braces and will not fill these"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_SLOTS))
def test_product_name_is_substituted(name):
    prompt, _ = EXPECTED_SLOTS[name]
    assert PRODUCT_NAME in prompt
    assert "{product}" not in prompt


@pytest.mark.parametrize("name", sorted(EXPECTED_SLOTS))
def test_render_fills_every_slot(name):
    prompt, expected = EXPECTED_SLOTS[name]
    variables = {slot: f"<{slot}>" for slot in expected}

    out = render(prompt, variables)

    assert not SLOT.search(out), f"{name} still has unfilled slots after render()"
    for slot in expected:
        assert f"<{slot}>" in out


def test_render_leaves_unknown_slots_alone():
    assert render("hi {{a}} and {{b}}", {"a": "1"}) == "hi 1 and {{b}}"


# --- the prompts and the machine's variables agree ------------------------
#
# render() is deliberately forgiving, so a slot the machine never supplies
# would reach the callee verbatim rather than raising. These check the two
# sides stay in sync.


def contexts(alert):
    return {
        "user": _user_context(alert),
        "kin": _kin_context(alert, alert.kin[0], "no_answer"),
        "support": _support_context(alert, alert.support_contact, "no_answer"),
    }


@pytest.mark.parametrize("role", ["user", "kin", "support"])
def test_machine_supplies_every_slot_the_prompt_uses(jeff_alert, role):
    ctx = contexts(jeff_alert)[role]
    missing = set(SLOT.findall(ctx.system_prompt)) - set(ctx.variables)
    assert not missing, f"{role} prompt uses slots the machine never sets: {sorted(missing)}"


@pytest.mark.parametrize("role", ["user", "kin", "support"])
def test_real_context_renders_clean(jeff_alert, role):
    """What ElevenLabs is handed: no braces left, and real values in place."""
    ctx = contexts(jeff_alert)[role]

    system = render(ctx.system_prompt, ctx.variables)
    opening = render(DRY_RUN_OPENINGS[role], ctx.variables)

    for filled in (system, opening):
        assert "{{" not in filled and "}}" not in filled
        assert "{user_name}" not in filled
        assert jeff_alert.user.name in filled
