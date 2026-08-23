"""Logs what a call would say instead of placing it.

Doubles as the scripted provider for tests: pass a `script` mapping phone
numbers to `ScriptedCall`s to drive any path through the ladder.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from ..models import CallContext
from ..prompts import DRY_RUN_OPENINGS, render
from .base import CallHandle, CallResult

log = logging.getLogger("escalation.dryrun")


@dataclass
class ScriptedCall:
    answered: bool = True
    transcript: str = ""
    outcome: Optional[str] = None
    confidence: float = 0.95
    grounded: bool = True
    timeout: bool = False
    error: Optional[str] = None


class DryRunProvider:
    """No audio, no telephony. Every call is logged and answered from a script."""

    name = "dryrun"

    def __init__(
        self,
        script: Optional[dict[str, ScriptedCall]] = None,
        default: Optional[ScriptedCall] = None,
    ):
        self.script = script or {}
        self.default = default if default is not None else ScriptedCall(answered=False)
        self.dialled: list[str] = []

    def place_call(self, ctx: CallContext) -> CallHandle:
        self.dialled.append(ctx.to_phone)
        opening = render(DRY_RUN_OPENINGS.get(ctx.role.value, ""), ctx.variables)
        log.info(
            "[DRY RUN] would dial %s (%s) as the %s call\n"
            "          it would say: %s",
            ctx.to_name,
            ctx.to_phone,
            ctx.role.value,
            opening,
        )
        return CallHandle(
            call_id=f"dry_{uuid.uuid4().hex[:8]}", ctx=ctx, provider=self.name
        )

    def await_result(
        self, handle: CallHandle, timeout_s: float
    ) -> Optional[CallResult]:
        spec = self.script.get(handle.ctx.to_phone, self.default)
        if spec.timeout:
            log.info("[DRY RUN] %s did not reach an outcome in %.0fs", handle.ctx.to_name, timeout_s)
            return None
        if not spec.answered:
            log.info("[DRY RUN] no answer from %s", handle.ctx.to_name)
            return CallResult(answered=False, error=spec.error)
        log.info("[DRY RUN] %s replied: %s", handle.ctx.to_name, spec.transcript or "(silence)")
        return CallResult(
            answered=True,
            transcript=spec.transcript,
            outcome=spec.outcome,
            confidence=spec.confidence,
            grounded=spec.grounded,
            error=spec.error,
        )
