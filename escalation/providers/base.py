"""The seam between the ladder and however a call actually happens.

Placing a call and awaiting its result are separate steps because the two
providers resolve on different timelines: dry run answers immediately, while
ElevenLabs answers asynchronously through a post-call webhook. Splitting them
lets the state machine treat both identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from ..models import CallContext


@dataclass
class CallHandle:
    """A call in flight. `external_id` is the provider's own reference."""

    call_id: str
    ctx: CallContext
    provider: str
    external_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CallResult:
    """What a finished call produced.

    `outcome` is set when the provider extracted the structured outcome itself
    (ElevenLabs post-call data collection does this). When it is None the
    transcript is classified by `escalation.agents` instead. Either way the
    result goes through the same validation.
    """

    answered: bool
    transcript: str = ""
    outcome: Optional[str] = None
    confidence: float = 0.95
    grounded: bool = True
    error: Optional[str] = None


@runtime_checkable
class CallProvider(Protocol):
    name: str

    def place_call(self, ctx: CallContext) -> CallHandle: ...

    def await_result(
        self, handle: CallHandle, timeout_s: float
    ) -> Optional[CallResult]:
        """None means the call ran past its deadline without an outcome."""
        ...
