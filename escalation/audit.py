"""The audit trail.

Every transition goes to two places: a human-readable terminal line for the
demo, and an append-only JSONL file so the trail survives the process.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from .models import AlertState, Transition

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"

STATE_COLOUR = {
    AlertState.DETECTED: "\033[37m",
    AlertState.CALLING_USER: "\033[36m",
    AlertState.CALLING_KIN: "\033[34m",
    AlertState.CALLING_SUPPORT: "\033[35m",
    AlertState.ESCALATED: "\033[31m",
    AlertState.RESOLVED: "\033[32m",
    AlertState.UNRESOLVED: "\033[33m",
}


def colour_enabled() -> bool:
    """Honours NO_COLOR and non-tty output."""
    if os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()


_colour_enabled = colour_enabled  # internal alias


class AuditLog:
    def __init__(self, path: Optional[str] = None, echo: bool = True):
        from .config import settings

        self.path = path if path is not None else settings.log_path
        self.echo = echo

    def record(self, transition: Transition) -> Transition:
        self._write(transition)
        if self.echo:
            print(self.format(transition), flush=True)
        return transition

    def _write(self, transition: Transition) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(transition.model_dump(mode="json")) + "\n")

    def format(self, t: Transition) -> str:
        use_colour = _colour_enabled()
        colour = STATE_COLOUR.get(t.to_state, "") if use_colour else ""
        reset = RESET if use_colour else ""
        dim = DIM if use_colour else ""
        bold = BOLD if use_colour else ""

        arrow = f"{t.from_state.value} -> {colour}{bold}{t.to_state.value}{reset}"
        parts = [f"{dim}{t.ts}{reset}", f"{dim}{t.alert_id}{reset}", arrow]
        if t.outcome:
            parts.append(f"outcome={t.outcome}")
        line = "  ".join(parts)
        if t.detail:
            line += f"\n    {dim}{t.detail}{reset}"
        if t.transcript_summary:
            line += f"\n    {dim}summary: {t.transcript_summary}{reset}"
        return line


class NullAuditLog(AuditLog):
    """Used by tests: keeps the record in memory, writes and prints nothing."""

    def __init__(self):
        self.path = None
        self.echo = False
