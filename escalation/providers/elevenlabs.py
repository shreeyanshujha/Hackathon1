"""ElevenLabs Conversational AI placing calls over Twilio.

The agent's job does not change here; only how the transcript arrives. An
outbound call is placed through ElevenLabs' Twilio integration, the per-call
details ride along as dynamic variables, and the outcome comes back
asynchronously on the post-call webhook.

Two ways to get the right prompt on the right call:

  1. Three agents, one per role — set ELEVENLABS_AGENT_ID_USER / _KIN /
     _SUPPORT. Nothing needs overriding.
  2. One agent, prompt overridden per call — set ELEVENLABS_AGENT_ID only.
     This needs `prompt.prompt` and `first_message` overrides enabled in the
     agent's Security settings, since ElevenLabs disables them by default.

For the structured outcome, configure these data-collection fields on the
agent (Analysis -> Data collection). Any that are missing simply fall back to
this service classifying the transcript itself:

    outcome                       string   the agent's recorded outcome
    confidence                    number   0..1
    summary                       string   one line
    answered_grounding_question   boolean  Agent A only
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import settings
from ..models import CallContext
from ..prompts import DRY_RUN_OPENINGS, render
from .base import CallHandle, CallResult

log = logging.getLogger("escalation.elevenlabs")


# How long a pending or orphaned entry is kept before being swept. Entries are
# normally removed the moment their call resolves; these two cases are what
# would otherwise leak:
#
#   * a call that timed out, whose entry is kept deliberately so a late webhook
#     can be recognised and ignored rather than resolving a call the ladder has
#     already moved past — but that webhook may never arrive;
#   * a webhook that arrived for a conversation nobody is waiting on.
#
# Neither matters at demo scale. The sweep exists so a long-running deployment
# does not accumulate them.
PENDING_TTL_SECONDS = 900.0


@dataclass
class _Pending:
    ctx: CallContext
    event: threading.Event
    result: Optional[CallResult] = None
    abandoned: bool = False
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class _Orphan:
    """A webhook result that landed before anyone registered to receive it."""

    result: CallResult
    created_at: float = field(default_factory=time.monotonic)


_PENDING: dict[str, _Pending] = {}
_ORPHANS: dict[str, _Orphan] = {}
_PENDING_LOCK = threading.Lock()


def _sweep(now: Optional[float] = None) -> None:
    now = now if now is not None else time.monotonic()
    with _PENDING_LOCK:
        for store in (_PENDING, _ORPHANS):
            stale = [
                key for key, entry in store.items()
                if now - entry.created_at > PENDING_TTL_SECONDS
            ]
            for key in stale:
                del store[key]
                log.debug("swept stale entry %s", key)


class ElevenLabsTwilioProvider:
    name = "elevenlabs"

    def __init__(self, client: Any = None):
        self._client = client
        missing = [
            key
            for key, value in {
                "ELEVENLABS_API_KEY": settings.elevenlabs_api_key,
                "ELEVENLABS_AGENT_ID": settings.elevenlabs_agent_id
                or settings.elevenlabs_agent_user,
                "ELEVENLABS_PHONE_NUMBER_ID": settings.elevenlabs_phone_number_id,
            }.items()
            if not value
        ]
        if missing and client is None:
            raise RuntimeError(
                "CALL_PROVIDER=elevenlabs but these are unset: "
                + ", ".join(missing)
                + ". Set them, or run with CALL_PROVIDER=dryrun."
            )

    @property
    def client(self):
        if self._client is None:
            from elevenlabs.client import ElevenLabs

            self._client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        return self._client

    def place_call(self, ctx: CallContext) -> CallHandle:
        _sweep()
        system_prompt = render(ctx.system_prompt, ctx.variables)
        first_message = render(DRY_RUN_OPENINGS.get(ctx.role.value, ""), ctx.variables)

        initiation: dict[str, Any] = {
            # Every {{slot}} in the agent's prompt is filled from here.
            "dynamic_variables": dict(ctx.variables),
        }
        if not _has_dedicated_agent(ctx):
            initiation["conversation_config_override"] = {
                "agent": {
                    "prompt": {"prompt": system_prompt},
                    "first_message": first_message,
                }
            }

        log.info("dialling %s (%s) as the %s call", ctx.to_name, ctx.to_phone, ctx.role.value)
        response = self.client.conversational_ai.twilio.outbound_call(
            agent_id=settings.agent_id_for(ctx.role.value),
            agent_phone_number_id=settings.elevenlabs_phone_number_id,
            to_number=ctx.to_phone,
            call_recording_enabled=True,
            conversation_initiation_client_data=initiation,
        )

        conversation_id = getattr(response, "conversation_id", None)
        if not conversation_id:
            raise RuntimeError(f"ElevenLabs did not return a conversation_id: {response!r}")

        # The conversation_id only exists once the API has responded, so a call
        # that fails immediately can have its webhook land before this point.
        # Claim any result that already arrived instead of dropping it and
        # making await_result sit out the whole timeout.
        pending = _Pending(ctx=ctx, event=threading.Event())
        with _PENDING_LOCK:
            _PENDING[conversation_id] = pending
            orphan = _ORPHANS.pop(conversation_id, None)
        if orphan is not None:
            log.info(
                "webhook for %s arrived before registration; applying it now",
                conversation_id,
            )
            pending.result = orphan.result
            pending.event.set()

        return CallHandle(
            call_id=conversation_id,
            ctx=ctx,
            provider=self.name,
            external_id=getattr(response, "callSid", None) or getattr(response, "call_sid", None),
        )

    def await_result(self, handle: CallHandle, timeout_s: float) -> Optional[CallResult]:
        with _PENDING_LOCK:
            pending = _PENDING.get(handle.call_id)
        if pending is None:
            log.warning("no pending record for %s; treating as no answer", handle.call_id)
            return CallResult(answered=False, error="no pending record")

        if pending.event.wait(timeout_s):
            with _PENDING_LOCK:
                _PENDING.pop(handle.call_id, None)
            return pending.result

        # Deadline hit. Keep the entry so a late webhook can be logged and
        # ignored rather than resolving a call the ladder has moved past.
        pending.abandoned = True
        log.info("%s did not report an outcome within %.0fs", handle.ctx.to_name, timeout_s)
        return None


def _has_dedicated_agent(ctx: CallContext) -> bool:
    return bool(
        {
            "user": settings.elevenlabs_agent_user,
            "kin": settings.elevenlabs_agent_kin,
            "support": settings.elevenlabs_agent_support,
        }.get(ctx.role.value)
    )


# --- webhook --------------------------------------------------------------


def _verify(raw: bytes, signature: Optional[str]) -> dict:
    secret = settings.elevenlabs_webhook_secret
    if not secret:
        log.warning(
            "ELEVENLABS_WEBHOOK_SECRET is unset; accepting the webhook without "
            "verifying its signature. Do not run it this way outside a demo."
        )
        return json.loads(raw.decode("utf-8"))

    from elevenlabs.client import ElevenLabs
    from elevenlabs.errors import BadRequestError

    try:
        return ElevenLabs(api_key=settings.elevenlabs_api_key).webhooks.construct_event(
            rawBody=raw.decode("utf-8"), sig_header=signature, secret=secret
        )
    except BadRequestError as exc:
        raise PermissionError(f"invalid ElevenLabs signature: {exc}") from exc


def _flatten_transcript(turns: list[dict]) -> str:
    lines = []
    for turn in turns or []:
        role = turn.get("role", "?")
        message = (turn.get("message") or "").strip()
        if message:
            lines.append(f"{role}: {message}")
    return "\n".join(lines)


def _collected(results: Any, key: str) -> Any:
    """Data collection entries are usually {"value": ..., "rationale": ...}."""
    if not isinstance(results, dict):
        return None
    entry = results.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def handle_post_call_webhook(raw: bytes, signature: Optional[str]) -> Optional[str]:
    """Resolve the call this webhook belongs to. Returns its conversation_id."""
    event = _verify(raw, signature)
    event_type = event.get("type")
    data = event.get("data") or {}
    conversation_id = data.get("conversation_id") or event.get("conversation_id")

    if not conversation_id:
        log.warning("webhook %s carried no conversation_id", event_type)
        return None

    if event_type == "call_initiation_failure":
        reason = data.get("failure_reason", "unknown")
        _resolve(conversation_id, CallResult(answered=False, error=f"call failed: {reason}"))
        return conversation_id

    if event_type != "post_call_transcription":
        log.debug("ignoring webhook of type %s", event_type)
        return None

    transcript = _flatten_transcript(data.get("transcript") or [])
    collection = data.get("data_collection_results") or {}
    analysis = data.get("analysis") or {}

    outcome = _collected(collection, "outcome")
    confidence = _collected(collection, "confidence")
    grounded = _collected(collection, "answered_grounding_question")
    summary = _collected(collection, "summary") or analysis.get("transcript_summary") or ""

    result = CallResult(
        answered=bool(transcript.strip()),
        transcript=transcript or summary,
        outcome=str(outcome) if outcome else None,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.9,
        grounded=bool(grounded) if grounded is not None else False,
    )
    _resolve(conversation_id, result)
    return conversation_id


def _resolve(conversation_id: str, result: CallResult) -> None:
    with _PENDING_LOCK:
        pending = _PENDING.get(conversation_id)

    if pending is None:
        # Either the registration has not happened yet (see place_call) or
        # nobody is waiting. Hold it briefly so the former case can claim it.
        with _PENDING_LOCK:
            _ORPHANS[conversation_id] = _Orphan(result=result)
        log.info(
            "webhook for %s has no waiting call; held in case its registration "
            "is still in flight",
            conversation_id,
        )
        return
    if pending.abandoned:
        log.info(
            "late webhook for %s (%s); the ladder already moved on, so it is "
            "logged but not acted on",
            conversation_id,
            pending.ctx.to_name,
        )
        with _PENDING_LOCK:
            _PENDING.pop(conversation_id, None)
        return

    pending.result = result
    pending.event.set()
