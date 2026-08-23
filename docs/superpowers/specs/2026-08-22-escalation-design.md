# Emergency Escalation Agent System — Design

Date: 2026-08-22

This document records only what is **not** already in the build brief. The brief
supplies the input contract, the state diagram, the three system prompts, the
phase plan, and the definition of done; treat it as the requirements document.
What follows are the design decisions made on top of it.

## Decisions taken during brainstorming

| # | Question the brief left open | Decision |
|---|---|---|
| 1 | What decides an outcome from a transcript in Phase 1? | Claude (`claude-haiku-4-5`) via the Anthropic Python SDK with a forced JSON schema, plus a deterministic fallback classifier used when no API key is set or the call fails. |
| 2 | How far does Phase 3 go now? | All calls go through a `CallProvider` interface. Two implementations ship: `DryRunProvider` and `ElevenLabsTwilioProvider`, selected by the `CALL_PROVIDER` env var. |
| 3 | Kin returns `unclear` — where does it go? | Treated like `no_answer`: advance to the next kin. Only when the list is exhausted does the ladder move to `calling_support`. |
| 4 | Support returns `unclear` — the diagram omits this edge | `escalated` (simulated ambulance). Support is the top rung, so "ambiguity resolves upward" has nowhere else to point. Logged with reason `no clear decision at final rung`. |

## Architecture

```
escalation/
  models.py        Alert, UserProfile, Kin, states, outcomes, Transition
  config.py        tier -> timeout/confidence tables, env settings
  prompts.py       AGENT_A_SYSTEM, AGENT_B_SYSTEM, AGENT_SUPPORT_SYSTEM
  llm.py           Anthropic client, forced-JSON call, rule fallback
  agents.py        classify_user_call(), classify_relay_call(), validation
  providers/
    base.py        CallProvider protocol, CallContext, CallHandle, CallResult
    dryrun.py      logs what a call would say; scripted replies
    elevenlabs.py  ElevenLabs ConvAI outbound over Twilio + post-call webhook
  machine.py       run_alert(): drives detected -> ... -> terminal state
  audit.py         transition log -> terminal + logs/transitions.jsonl
  generator.py     fake alerts matching the input contract
  api.py           FastAPI app: intake, /trigger, status, webhook
```

### The provider seam

Dry run resolves an outcome synchronously; ElevenLabs resolves it
asynchronously via a post-call webhook. One interface covers both by splitting
placing a call from awaiting its result:

```python
place_call(ctx) -> CallHandle
await_result(handle, timeout_s) -> CallResult | None   # None means timeout
```

`CallResult` always carries a transcript and may carry a pre-extracted outcome
(ElevenLabs post-call data extraction can return the JSON itself). When the
outcome is present it is validated; when only a transcript is present it is run
through the same Phase 1 classifier. The agents' decision logic is therefore
identical in both modes — only the source of the transcript changes.

### Two Claude layers

| Layer | Runs where | Model |
|---|---|---|
| Live phone conversation (Phase 3) | Inside the ElevenLabs agent | Claude Haiku, set in ElevenLabs' LLM settings |
| Transcript to structured outcome | This codebase, Anthropic Python SDK | `claude-haiku-4-5` (`CLASSIFIER_MODEL`) |

### "Never default to resolved", enforced structurally

The rule is a code invariant, not only prompt text:

1. `validate_user_decision()` downgrades `resolved_ok` to `unclear` when
   confidence is below the tier's threshold or the grounding question was not
   answered.
2. The deterministic fallback classifier has no branch that returns
   `resolved_ok`.
3. Any exception, malformed JSON, or unrecognised outcome maps to `unclear`.

A prompt regression, an API outage, and a garbled response all fail toward
escalation.

### Tier

Tier changes timeouts and the confidence bar only; the flow is identical across
tiers.

| Tier | Call timeout | Confidence needed to resolve |
|---|---|---|
| 1 | 60s | 0.60 |
| 2 | 45s | 0.70 |
| 3 | 30s | 0.80 |

`DEMO_MODE=1` clamps every timeout to 10s.

### Ambulance safety

`escalated` calls `simulate_ambulance()`, which writes a record labelled
`*** SIMULATED — NO REAL CALL PLACED ***`. No code path dials an emergency
number, and a test asserts that no provider is ever handed one.

### Error handling

| Failure | Behaviour |
|---|---|
| Claude call errors or returns junk | Deterministic fallback; never `resolved_ok` |
| Provider fails to place a call | Logged, treated as `no_answer`, ladder continues |
| Webhook arrives after its timeout | Logged as late, ignored for state |
| Unknown `alert_id` on webhook | 404, logged |

## Out of scope

Detection logic, onboarding UI, real emergency dispatch, consent handling
(kin are assumed to have consented at onboarding — this must be built before
any real deployment).
