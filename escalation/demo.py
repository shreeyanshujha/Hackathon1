"""Terminal demo. `python -m escalation.demo --help`

Two modes:

  scripted     canned replies, so a full ladder runs in a couple of seconds
  interactive  you type the replies, which go through the real classifier

Nothing here places a call.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from typing import Optional

from dotenv import load_dotenv

# Must run before the escalation imports below: Settings reads the environment
# at import time. Shell variables still win over .env.
load_dotenv()

from .config import reload_settings, settings  # noqa: E402
from .generator import make_alert
from .machine import run_alert
from .models import AlertState, CallContext
from .prompts import DRY_RUN_OPENINGS, render
from .providers.base import CallHandle, CallResult
from .providers.dryrun import DryRunProvider, ScriptedCall
from .scenarios import BLURBS, ScenarioName, script_for

from .audit import colour_enabled


def _c(code: str) -> str:
    return code if colour_enabled() else ""


BOLD, DIM, RESET = _c("\033[1m"), _c("\033[2m"), _c("\033[0m")
GREEN, RED, AMBER = _c("\033[32m"), _c("\033[31m"), _c("\033[33m")


def _numbers():
    return settings.jeff_phone, settings.jess_phone, settings.support_phone


def scenarios() -> dict[str, dict]:
    return {
        name.value: {"blurb": BLURBS[name], "script": script_for(name)}
        for name in ScenarioName
    }


class InteractiveProvider:
    """You are the person on the other end. Replies go to the real classifier."""

    name = "interactive"

    def __init__(self):
        self.dialled: list[str] = []

    def place_call(self, ctx: CallContext) -> CallHandle:
        opening = render(DRY_RUN_OPENINGS.get(ctx.role.value, ""), ctx.variables)
        self.dialled.append(ctx.to_phone)
        print(f"\n{BOLD}--- calling {ctx.to_name} ({ctx.to_phone}) ---{RESET}")
        print(f"{DIM}agent:{RESET} {opening}")
        return CallHandle(call_id=f"int_{uuid.uuid4().hex[:6]}", ctx=ctx, provider=self.name)

    def await_result(self, handle: CallHandle, timeout_s: float) -> Optional[CallResult]:
        prompt = (
            f"{BOLD}{handle.ctx.to_name}{RESET} (enter = no answer, "
            f"'timeout' = ran out of time): "
        )
        try:
            reply = input(prompt).strip()
        except EOFError:
            reply = ""
        if reply.lower() == "timeout":
            return None
        if not reply:
            return CallResult(answered=False)
        # No outcome supplied, so the transcript goes through the classifier.
        return CallResult(answered=True, transcript=reply)


def banner(alert, label: str, blurb: str) -> None:
    print(f"\n{BOLD}{'=' * 78}{RESET}")
    print(f"{BOLD}  {label}{RESET}  {DIM}{blurb}{RESET}")
    print(
        f"  {alert.user.name}, {alert.user.age}, tier {alert.tier} | "
        f"still {alert.detail.stillness_minutes} min during "
        f"'{alert.user.expected_activity}' | "
        f"HR {alert.user.hr_baseline} -> {alert.detail.hr_now}"
        + (" | on a beta blocker" if alert.user.on_beta_blocker else "")
    )
    print(f"{BOLD}{'=' * 78}{RESET}")


VERDICT = {
    AlertState.RESOLVED: f"{GREEN}RESOLVED{RESET} — no escalation needed",
    AlertState.ESCALATED: f"{RED}ESCALATED{RESET} — simulated ambulance, no real call placed",
    AlertState.UNRESOLVED: f"{AMBER}UNRESOLVED{RESET} — chain exhausted with no decision",
}


def run_one(label: str, spec: dict, tier: int) -> float:
    alert = make_alert(tier=tier)
    banner(alert, label, spec["blurb"])
    provider = DryRunProvider(spec["script"], default=ScriptedCall(answered=False))
    started = time.monotonic()
    run = run_alert(alert, provider)
    elapsed = time.monotonic() - started
    print(f"\n  {VERDICT.get(run.state, run.state.value)}")
    print(f"  {DIM}{len(run.transitions)} transitions, {len(run.calls)} calls, "
          f"{elapsed:.2f}s{RESET}")
    return elapsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Escalation ladder demo. Places no calls.")
    parser.add_argument(
        "--scenario", default="all",
        choices=[*scenarios().keys(), "all"],
        help="which path to run (default: all)",
    )
    parser.add_argument("--tier", type=int, default=3, choices=[1, 2, 3])
    parser.add_argument(
        "--interactive", action="store_true",
        help="type the replies yourself; they go through the real classifier",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    reload_settings()

    if args.interactive:
        alert = make_alert(tier=args.tier)
        banner(alert, "INTERACTIVE", "you are Jeff, then Jess, then support")
        run = run_alert(alert, InteractiveProvider())
        print(f"\n  {VERDICT.get(run.state, run.state.value)}")
        return 0

    chosen = scenarios() if args.scenario == "all" else {args.scenario: scenarios()[args.scenario]}
    total = sum(run_one(label, spec, args.tier) for label, spec in chosen.items())
    print(f"\n{DIM}{len(chosen)} scenario(s) in {total:.2f}s total. "
          f"Audit trail: {settings.log_path}{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
