"""Core vocabulary: states, outcomes, the alert contract, and the audit record."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AlertState(str, Enum):
    DETECTED = "detected"
    CALLING_USER = "calling_user"
    CALLING_KIN = "calling_kin"
    CALLING_SUPPORT = "calling_support"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


TERMINAL_STATES = frozenset(
    {AlertState.ESCALATED, AlertState.RESOLVED, AlertState.UNRESOLVED}
)


class UserOutcome(str, Enum):
    """Agent A. The only outcome that closes an alert is RESOLVED_OK."""

    RESOLVED_OK = "resolved_ok"
    NO_ANSWER = "no_answer"
    UNCLEAR = "unclear"
    TIMEOUT = "timeout"


class RelayOutcome(str, Enum):
    """Agent B and the support fallback share this set."""

    AMBULANCE_REQUESTED = "ambulance_requested"
    DECLINED = "declined"
    NO_ANSWER = "no_answer"
    UNCLEAR = "unclear"


class CallRole(str, Enum):
    USER = "user"
    KIN = "kin"
    SUPPORT = "support"


# --- input contract -------------------------------------------------------


class UserProfile(BaseModel):
    name: str
    age: int
    on_beta_blocker: bool = False
    hr_baseline: int
    expected_activity: str
    phone: Optional[str] = None


class Kin(BaseModel):
    name: str
    phone: str


class SupportContact(BaseModel):
    phone: str
    name: str = "the on-call support line"


class Detail(BaseModel):
    stillness_minutes: int
    hr_now: int


class Alert(BaseModel):
    alert_id: str
    user: UserProfile
    reason: str = "still_with_rising_hr"
    detail: Detail
    tier: int = Field(default=1, ge=1, le=3)
    kin: list[Kin] = Field(default_factory=list)
    # Fixed at the product level, so it may be absent from the payload and
    # filled in from config.
    support_contact: Optional[SupportContact] = None


# --- agent output ---------------------------------------------------------


class AgentDecision(BaseModel):
    """The fixed shape every call returns, whatever produced it."""

    outcome: str
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    source: str = "llm"  # llm | fallback | provider | machine


class UserCallDecision(BaseModel):
    """Schema handed to Claude for an Agent A transcript. LLM-facing."""

    outcome: UserOutcome
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    answered_grounding_question: bool


class RelayCallDecision(BaseModel):
    """Schema handed to Claude for a kin or support transcript. LLM-facing."""

    outcome: RelayOutcome
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str


# --- audit ----------------------------------------------------------------


class Transition(BaseModel):
    alert_id: str
    ts: str = Field(default_factory=utcnow_iso)
    from_state: AlertState
    to_state: AlertState
    outcome: Optional[str] = None
    actor: str = "system"
    detail: str = ""
    transcript_summary: Optional[str] = None
    transcript: Optional[str] = None
    simulated: bool = False

    def line(self) -> str:
        arrow = f"{self.from_state.value} -> {self.to_state.value}"
        bits = [self.ts, self.alert_id, arrow]
        if self.outcome:
            bits.append(f"outcome={self.outcome}")
        if self.detail:
            bits.append(self.detail)
        return "  ".join(bits)


class CallRecord(BaseModel):
    alert_id: str
    role: CallRole
    to_name: str
    to_phone: str
    ts: str = Field(default_factory=utcnow_iso)
    dry_run: bool = False
    transcript: str = ""
    decision: Optional[AgentDecision] = None
    error: Optional[str] = None


class AlertRun(BaseModel):
    alert: Alert
    state: AlertState = AlertState.DETECTED
    transitions: list[Transition] = Field(default_factory=list)
    calls: list[CallRecord] = Field(default_factory=list)
    started_at: str = Field(default_factory=utcnow_iso)
    finished_at: Optional[str] = None
    ambulance_simulated: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def summary(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert.alert_id,
            "state": self.state.value,
            "ambulance_simulated": self.ambulance_simulated,
            "transitions": len(self.transitions),
            "calls": len(self.calls),
        }


class CallContext(BaseModel):
    """Everything one outbound call needs.

    The same object serves three consumers: the provider (phone number and
    prompt), the ElevenLabs adapter (`variables` maps straight onto its custom
    per-call variables), and the agents (tier, for the confidence bar).
    """

    alert_id: str
    role: CallRole
    to_name: str
    to_phone: str
    system_prompt: str
    variables: dict[str, str] = Field(default_factory=dict)
    tier: int = 1
    timeout_s: float = 60.0
