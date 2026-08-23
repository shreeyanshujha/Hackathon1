"""Transcript -> structured outcome, via Claude.

This is the classifier layer, distinct from the LLM ElevenLabs runs inside a
live call. It takes a finished transcript and applies the agent's own system
prompt to it. Returns None on any failure so the caller can fall back; it never
raises into the ladder.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from .config import settings

log = logging.getLogger("escalation.llm")

T = TypeVar("T", bound=BaseModel)

JUDGE_SUFFIX = """

---
You are not placing this call. The call has already happened, and its
transcript follows. Classify what happened and record the structured outcome by
applying exactly the rules above. Judge only what the transcript actually
shows. Do not invent detail that is not there, and do not give the caller the
benefit of the doubt: when the transcript is ambiguous, choose the outcome that
escalates.
"""

GROUNDING_NOTE = """
Set answered_grounding_question to true only when the person gave a coherent,
on-topic answer about what they are doing right now. A generic "I'm fine",
"yes", or a repeated greeting is not such an answer.
"""

# Where `ant auth login` stores its profiles. The SDK reads these with no env
# var set, so gating on ANTHROPIC_API_KEY alone would wrongly disable the
# classifier for anyone authenticated that way.
PROFILE_DIR = Path.home() / ".config" / "anthropic"

_client: Any = None


def set_client(client: Any) -> None:
    """Inject a client. Tests use this; production resolves credentials itself."""
    global _client
    _client = client


def credentials_available() -> bool:
    """True if the SDK has some credential to resolve.

    Checked before calling so a keyless demo falls back instantly instead of
    waiting on a network round trip that is going to 401.
    """
    if settings.anthropic_api_key or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    return PROFILE_DIR.exists()


def _get_client():
    global _client
    if _client is None:
        import anthropic

        # No api_key argument: let the SDK resolve env var, auth token, or profile.
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    return _client


def claude_decider(system: str, transcript: str, schema: Type[T]) -> Optional[T]:
    """Ask Claude to classify one transcript. None means "could not decide"."""
    if not credentials_available():
        log.debug("no Anthropic credentials resolvable; using deterministic fallback")
        return None

    response = _get_client().messages.parse(
        model=settings.classifier_model,
        max_tokens=512,
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"Call transcript:\n\n{transcript.strip()}\n\nRecord the outcome.",
            }
        ],
        output_format=schema,
        timeout=settings.classifier_timeout,
    )
    return response.parsed_output
