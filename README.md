# Emergency Escalation Agent System

Everything that happens *after* a wearable alert is raised: two voice agents, a
support fallback, and the state machine that ties them together.

Detection is out of scope — the sensor module hands this service a JSON alert.
`escalation/generator.py` produces fake ones so nothing waits on that module.

## The ladder

```
detected
  -> calling_user                       Agent A dials the user
    -> resolved                           resolved_ok
    -> calling_kin                        no_answer | unclear | timeout
      -> escalated                          kin says yes  -> SIMULATED ambulance
      -> resolved                           kin says no
      -> calling_kin[next]                  kin no answer or unclear
      -> calling_support                    kin list exhausted
        -> escalated                          support says yes, or is unclear
        -> resolved                           support says no
        -> unresolved                         support unreachable — true dead end
```

Two routing rules the diagram in the brief left open:

- **Kin `unclear`** is treated like `no_answer` — ask the next person. Only an
  exhausted list reaches `calling_support`.
- **Support `unclear`** escalates. Support is the top rung, so "ambiguity
  resolves upward" has nowhere else to point.

## Ambulance is always simulated

`escalated` writes a record labelled `*** SIMULATED — NO REAL CALL PLACED ***`.
No code path dials an emergency number, and `tests/test_machine.py` asserts it.

## Quick start

Needs Python 3.12+ (the Anthropic SDK requires 3.10+; macOS ships 3.9).
`uv` is the quickest way to get one:

```bash
uv python install 3.12 && uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env          # defaults are demo-safe: dry run, 10s timeouts
```

Then:

```bash
.venv/bin/python -m pytest              # 59 tests, phases 1-3
.venv/bin/python -m escalation.demo     # all five paths, no phone calls
.venv/bin/python -m escalation.demo --interactive   # you type the replies
.venv/bin/python -m uvicorn escalation.api:app --port 8000
```

```bash
curl -X POST "localhost:8000/trigger?wait=true"                   # -> escalated
curl -X POST "localhost:8000/trigger?wait=true&scenario=support"  # kin -> support
open http://localhost:8000                                        # live log page
```

| Endpoint | Purpose |
|---|---|
| `POST /trigger` | Fake alert on demand. `?scenario=kin\|support\|resolved\|unclear\|unresolved`, `?tier=1..3`, `?wait=true` |
| `POST /alerts` | Intake for the detection module's payload |
| `GET /alerts/{id}` | Full transition log and call records |
| `GET /` | Auto-refreshing log page |
| `POST /webhooks/elevenlabs/post-call` | Phase 3 outcome callback |

## Phases

| Phase | What | Status |
|---|---|---|
| 1 | Transcript in, structured outcome out | `escalation/agents.py` |
| 2 | State machine, intake, transition logging | `escalation/machine.py` |
| 3 | ElevenLabs Conversational AI over Twilio | `escalation/providers/elevenlabs.py`, set `CALL_PROVIDER=elevenlabs` |
| 4 | Dry run, `/trigger`, 10s stage timeouts | `DEMO_MODE=1` (default) |

## Two Claude layers

| Layer | Runs where | Model |
|---|---|---|
| The live phone conversation | Inside the ElevenLabs agent | Claude Haiku, chosen in ElevenLabs' LLM settings |
| Transcript to structured outcome | This service, Anthropic Python SDK | `claude-haiku-4-5` |

Without `ANTHROPIC_API_KEY` the second layer uses a deterministic fallback that
**cannot return `resolved_ok` or `declined`** — so an outage can never close an
alert or stand the ladder down. It can only fail upward.

## Tier

Tier changes timeouts and the confidence bar. It never changes the flow.

| Tier | Call timeout | Confidence to resolve |
|---|---|---|
| 1 (no conditions) | 60s | 0.60 |
| 2 (has a condition) | 45s | 0.70 |
| 3 (recent event / rate-flattening medication) | 30s | 0.80 |

`DEMO_MODE=1` clamps every timeout to 10s.

## Not built, and needed before this is real

Kin consent is **assumed** — the brief says onboarding handles it. Nothing here
records, checks, or expires that consent, and the agents disclose health
information over the phone. That gap has to close before any real deployment.
