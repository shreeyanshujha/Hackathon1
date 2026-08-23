"""Environment-driven settings and the tier tables.

Tier changes timeouts and the confidence bar only. It never changes the flow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PRODUCT_NAME = os.getenv("PRODUCT_NAME", "Halo")

# Seconds a single call may run before the ladder gives up on it.
TIER_CALL_TIMEOUT = {1: 60.0, 2: 45.0, 3: 30.0}

# How sure Agent A must be before an alert is allowed to close. A tier 3
# profile (recent event, or medication that flattens heart rate) has to clear a
# higher bar than someone with no conditions.
TIER_RESOLVE_CONFIDENCE = {1: 0.60, 2: 0.70, 3: 0.80}

DEMO_TIMEOUT_SECONDS = 10.0


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    demo_mode: bool = field(default_factory=lambda: _flag("DEMO_MODE", "1"))
    call_provider: str = field(
        default_factory=lambda: os.getenv("CALL_PROVIDER", "dryrun").strip().lower()
    )

    # Classification (Phase 1/2, and the fallback when a webhook returns only a
    # transcript). Distinct from the LLM ElevenLabs runs inside the live call.
    classifier_model: str = field(
        default_factory=lambda: os.getenv("CLASSIFIER_MODEL", "claude-haiku-4-5")
    )
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None
    )
    # The SDK default is minutes. A classification is one short call sitting in
    # the middle of a live escalation, so it fails fast to the fallback instead.
    classifier_timeout: float = field(
        default_factory=lambda: float(os.getenv("CLASSIFIER_TIMEOUT", "5"))
    )

    # Demo numbers. Jeff is the user, Jess is kin, support is the on-call
    # line.
    jeff_phone: str = field(default_factory=lambda: os.getenv("JEFF_PHONE", "+61400000001"))
    jess_phone: str = field(default_factory=lambda: os.getenv("JESS_PHONE", "+61400000002"))
    support_phone: str = field(
        default_factory=lambda: os.getenv("SUPPORT_PHONE", "+61400000009")
    )

    # Phase 3 credentials.
    elevenlabs_api_key: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_API_KEY") or None
    )
    elevenlabs_agent_id: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_AGENT_ID") or None
    )
    elevenlabs_phone_number_id: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_PHONE_NUMBER_ID") or None
    )
    elevenlabs_webhook_secret: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_WEBHOOK_SECRET") or None
    )
    # Optional: a dedicated agent per role. Falls back to the single agent id
    # above with a per-call prompt override.
    elevenlabs_agent_user: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_AGENT_ID_USER") or None
    )
    elevenlabs_agent_kin: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_AGENT_ID_KIN") or None
    )
    elevenlabs_agent_support: str | None = field(
        default_factory=lambda: os.getenv("ELEVENLABS_AGENT_ID_SUPPORT") or None
    )
    twilio_account_sid: str | None = field(
        default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID") or None
    )
    twilio_auth_token: str | None = field(
        default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN") or None
    )
    twilio_from_number: str | None = field(
        default_factory=lambda: os.getenv("TWILIO_FROM_NUMBER") or None
    )

    log_path: str = field(
        default_factory=lambda: os.getenv("TRANSITION_LOG", "logs/transitions.jsonl")
    )

    def call_timeout(self, tier: int) -> float:
        base = TIER_CALL_TIMEOUT.get(tier, 60.0)
        return min(base, DEMO_TIMEOUT_SECONDS) if self.demo_mode else base

    def agent_id_for(self, role: str) -> str | None:
        per_role = {
            "user": self.elevenlabs_agent_user,
            "kin": self.elevenlabs_agent_kin,
            "support": self.elevenlabs_agent_support,
        }.get(role)
        return per_role or self.elevenlabs_agent_id

    def resolve_confidence(self, tier: int) -> float:
        return TIER_RESOLVE_CONFIDENCE.get(tier, 0.70)


settings = Settings()


def reload_settings() -> Settings:
    """Re-read the environment, in place.

    Modules import this object once (`from .config import settings`), so a
    rebind here would leave every importer holding the stale copy. Updating the
    existing instance means the CLI's post-load_dotenv reload and the test
    fixtures are actually seen everywhere.
    """
    settings.__dict__.update(Settings().__dict__)
    return settings
