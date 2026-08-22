# Mobile UI Agent — Engineering Plan

**Target app:** Hinge (Android)
**Pattern:** perception → state → judgment → action
**Status:** pre-Phase-0
**Owner:** Sage

---

## 0. What this document is

A build plan for an autonomous agent that drives a real Android device, reads dating-app
profiles, scores them, and takes actions. The Hinge-specific parts are the least valuable
parts of the project; the capture harness, state machine, and offline judgment layer are
the reusable skeleton and should be built as if the target app were swappable.

Read section 2 before writing any code. It contains the constraints that determine the
architecture, and two of them are hard blockers if ignored.

---

## 1. Scope

### In scope
- Physical-device control over ADB
- Full-profile capture (screenshots + view hierarchy, stitched)
- Deterministic screen-state detection and transition control
- VLM-based profile scoring, developed offline against a saved corpus
- Like/skip action execution with dry-run gating
- Draft generation for openers, with a human approval gate on send

### Out of scope (v1)
- Emulators (see 2.1)
- Private API / HTTP client (see 2.2)
- Autonomous conversation beyond the first message
- Multi-account operation
- Any autonomous send without approval (see 2.4)

---

## 2. Constraints and risk register

### 2.1 Emulators fail integrity checks — BLOCKER
Match Group apps call Play Integrity. Standard AVD, Bluestacks, Genymotion, LDPlayer all
fail `MEETS_DEVICE_INTEGRITY` and are additionally detectable via:

- `ro.kernel.qemu`, `ro.hardware=goldfish/ranchu`, `ro.product.model` build props
- SwiftShader / `Android Emulator OpenGL ES` GL renderer strings
- Absent or synthetic telephony stack (no IMEI, generic IMSI)
- Missing/perfectly-static accelerometer, gyro, magnetometer
- Battery reporting 100% at a fixed temperature forever

Google Play Games emulator and rooted-with-Magisk-DenyList setups get partway there and
are an ongoing arms race. **Decision: physical device.** A secondhand Pixel 4a/5a or
mid-range Samsung on stock, unrooted, is ~$80–150 and eliminates the entire category of
problem. It isn't pretending to be a phone.

### 2.2 No API path
Cert pinning + TLS fingerprinting + device attestation. You can strip pinning (patched
APK, Frida hooks on `TrustManagerImpl` / OkHttp) and observe traffic, but a synthetic
client cannot mint valid attestation tokens. Arkose challenges arrive quickly. UI
automation is the only viable surface.

### 2.3 Ban mechanics
Bans link device ID, phone number, and payment method. They are sticky across
re-registration. Treat account loss as permanent and unrecoverable, and set behavioural
budgets accordingly (section 9).

### 2.4 Send gate — design requirement, not a preference
An outbound message is the only irreversible side effect in the loop. No undo, and a real
human receives it. Agent drafts, human approves, system sends. Keep this even after
everything else is autonomous. The marginal time cost is seconds; the cost of a bad
autonomous send is a person on the other end and no way to retract.

### 2.5 Expected value is genuinely poor
Free-tier likes are capped in single digits per day. Hinge's matcher is Gale-Shapley
derived and learns preference from like patterns — indiscriminate liking degrades both
your recommendation quality and your position in others' queues. Volume is the one
variable the system punishes. Build this for the systems problem, not for the outcomes.

### 2.6 ToS
Automation violates Hinge's terms. Stated for the record; it informs 2.3.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator (state machine — deterministic)           │
│  owns: screen identity, transitions, budgets, recovery  │
└───────┬──────────────────────────────┬──────────────────┘
        │                              │
┌───────▼────────────┐        ┌────────▼─────────────────┐
│ Perception         │        │ Action                   │
│ • screencap        │        │ • tap / swipe / type     │
│ • dump_hierarchy   │        │ • humanisation layer     │
│ • stitch + parse   │        │ • post-action assert     │
└───────┬────────────┘        └──────────────────────────┘
        │
┌───────▼────────────────────────────────────────┐
│ Judgment (VLM — stateless, offline-testable)   │
│ in: ProfileRecord   out: ScoredProfile (JSON)  │
└───────┬────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────┐
│ Draft + Approval Queue (human in loop)         │
└────────────────────────────────────────────────┘
```

### Separation of responsibilities — the load-bearing decision

The single most common failure in screen-agent projects is letting the vision model own
navigation. It drifts: the model periodically believes it is on a screen it is not, and
taps through a paywall or types into a search field. Split it:

| Concern | Owner | Why |
|---|---|---|
| Which screen am I on? | View hierarchy + predicates | Deterministic, cheap, testable |
| Where is element X? | Hierarchy bounds | Exact pixels, no estimation |
| What does the text say? | Hierarchy text nodes | Free, no OCR error |
| Is this person a fit? | VLM | Actually needs judgment |
| Which photo/prompt to hook? | VLM | Actually needs judgment |
| Should I proceed? | State machine | Must be auditable |

The VLM never returns coordinates. It returns semantic selections (`"prompt_index": 2`)
which the orchestrator resolves to bounds via the hierarchy.

---

## 4. Environment setup

### Hardware
- Physical Android, unrooted, stock ROM, Android 11+
- Dedicated SIM / number (ban isolation)
- Wired ADB for setup; `adb tcpip 5555` for run-time so the phone can sit on a shelf

### Host
```bash
python -m venv .venv && source .venv/bin/activate
pip install uiautomator2 opencv-python pillow pydantic anthropic
python -m uiautomator2 init          # pushes atx-agent to device
pip install weditor                  # optional inspector UI
```

Verify:
```bash
adb devices
python -c "import uiautomator2 as u2; print(u2.connect().info)"
```

---

## 5. Phase 0 — Feasibility spike (1–2 hours, do this first)

**This decides the architecture. Do not skip it and do not build anything else first.**

```python
import uiautomator2 as u2
d = u2.connect()
# open Hinge to a profile manually, then:
open("dump.xml", "w", encoding="utf-8").write(d.dump_hierarchy())
```

Inspect `dump.xml`. Three possible outcomes:

**A — Rich tree.** Real `resource-id` values, `text` attributes populated with prompt
answers, distinct class names. Best case: profile text is free, element bounds are exact,
VLM is needed only for photos. Proceed as planned.

**B — Flat tree.** Wall of `android.view.View` / `FlutterView` with no ids and no text.
Hierarchy is dead weight. You are pure-vision: OCR (PaddleOCR or the VLM itself) for text,
template matching or VLM for element location. Materially harder, slower, more expensive
per profile. Still doable — reassess whether it's worth it.

**C — Mixed.** Chrome (nav bar, buttons) has ids; content is opaque. Common with React
Native. Use the tree for navigation and state detection, vision for content. This is a
fine outcome.

Record the result as an ADR (section 13) before continuing.

---

## 6. Phase 1 — Capture harness (read-only)

**No taps. No likes. Scroll and record only.**

This is the highest-leverage component in the project. It produces an offline corpus so
that every later layer is developed against saved data instead of the live app. You do not
burn account behavioural budget debugging a JSON schema.

### Behaviour
1. Detect "on a profile detail screen"
2. Capture screenshot + hierarchy dump at scroll position 0
3. Scroll one viewport with overlap; capture again
4. Repeat until scroll position stops changing (bottom reached)
5. Write one `ProfileRecord` to disk

### Scroll-end detection
Compare consecutive hierarchy dumps or perceptual hash of screenshots. If unchanged after
a swipe, you are at the bottom. Cap iterations at ~12 to avoid infinite loops on a stuck
screen.

### Stitching
A Hinge profile is a vertical scroll of photo and prompt cards, not one screen. You need
the full thing assembled before judgment. Overlap-based stitching:

```python
# capture with ~20% viewport overlap, then match on the overlap band
# cv2.matchTemplate(prev[-band:], curr, cv2.TM_CCOEFF_NORMED)
# splice at best match offset
```

If Phase 0 gave you outcome A, stitching matters less — you can reconstruct content from
the hierarchy and use images only for photos.

### On-disk layout
```
corpus/
  2026-08-13T09-14-22Z_a3f9/
    meta.json           # ProfileRecord
    shot_000.png
    shot_001.png
    dump_000.xml
    dump_001.xml
    stitched.png
```

**Exit criteria:** 50+ profiles captured cleanly, zero taps issued, stitching verified by
eye on 10 random samples.

---

## 7. Phase 2 — State machine

Boring, and it will be the majority of the codebase. Skipping it produces an agent that
taps confidently on the wrong screen.

### Screens to enumerate
```
FEED
PROFILE_DETAIL
LIKE_COMPOSER          # comment attached to a specific photo/prompt
MATCH_NOTIFICATION
CHAT_LIST
CHAT_THREAD
OUT_OF_LIKES_MODAL
UPSELL_MODAL           # multiple variants — enumerate each
ADD_PHOTOS_NAG
PERMISSION_DIALOG
NETWORK_ERROR
UNKNOWN                # always have this, always recover to a known state
```

### Predicate pattern
```python
@dataclass
class Screen:
    name: str
    predicate: Callable[[Hierarchy], bool]
    recover: Callable[[Device], None] | None = None

def is_profile_detail(h: Hierarchy) -> bool:
    return h.has(resource_id="...like_button") and h.has(cls="...ProfileScroll")
```

Predicates must be mutually exclusive. Write a test that asserts exactly one predicate
fires for every dump in the Phase 1 corpus.

### Transition contract
Every action declares its expected post-state and verifies it:

```python
def act(device, action, expect: str, timeout=5.0):
    action(device)
    state = wait_for_state(device, timeout)
    if state != expect:
        raise TransitionError(f"expected {expect}, got {state}")
    return state
```

On `UNKNOWN`: screenshot, log, attempt `back`, re-detect, and if still unknown after two
attempts, halt the run. Halt is correct. Guessing is not.

### Interstitial handling
Realistically ~80% of the code. Upsell modals, out-of-likes dialogs, photo nags, rating
prompts, network flakes. Each needs a predicate and a dismissal path. Build a generic
`dismiss_if_present` sweep that runs before every intended action.

**Exit criteria:** replay all Phase 1 dumps through the classifier, 100% correct, no
`UNKNOWN`.

---

## 8. Phase 3 — Judgment layer (offline)

Stateless function: `ProfileRecord → ScoredProfile`. Developed and tuned entirely against
the Phase 1 corpus. Zero live calls during development.

### Output contract
```json
{
  "score": 0.0,
  "confidence": 0.0,
  "hook": {
    "type": "prompt | photo",
    "index": 0,
    "reason": "why this element is the strongest hook"
  },
  "flags": ["possible_bot", "age_outside_range", "unreadable"],
  "notes": "one line, internal only"
}
```

The VLM returns **semantic indices, never coordinates**. The orchestrator maps
`prompt[2]` → element bounds via the hierarchy. This keeps the model out of the
navigation path (section 3).

### Prompt design
- System prompt states the output is JSON only, no prose, no markdown fences
- Feed the stitched image plus extracted text as structured input
- Parse defensively: strip fences, `json.loads`, on failure retry once, then flag
  `unreadable` and skip rather than guessing

### Calibration
Score 50 corpus profiles yourself first. Then run the model. Compare. Iterate on the
prompt until agreement is acceptable — you have the STRIDE calibration instinct for this
already; treat it as a ranking problem, check Spearman correlation against your own
ordering rather than raw score deltas.

**Exit criteria:** rank correlation with your own scoring ≥ 0.7 on held-out corpus
profiles.

---

## 9. Phase 4 — Action layer

### Dry-run first
```python
class Actuator:
    def __init__(self, live: bool = False): ...
    def tap(self, x, y, label=""):
        if not self.live:
            log.info("DRY would tap %s at (%d,%d)", label, x, y)
            return
        ...
```

Run dry over live browsing for a full session. Diff the `would tap` log against your own
judgment on the same profiles. Iterate until it stops surprising you. Only then flip
`live=True`, and only for the like/skip path — messaging stays gated (2.4).

### Humanisation
Not paranoia — behavioural telemetry is cheap for them to collect and the patterns are
trivially separable if you don't.

- **Tap coordinates:** Gaussian jitter around element centre, σ ≈ 15% of element size,
  clipped to bounds
- **Swipes:** bezier path, 3+ intermediate points, variable duration 180–420ms
- **Dwell time:** sample from a long-tailed distribution — real reading time correlates
  with content length. Longer on profiles you like.
- **Session structure:** 5–20 minutes, 1–3 sessions/day, gaps sampled not fixed
- **Circadian:** no activity 01:00–07:00 local. Weight toward evening.
- **Imperfection:** occasional scroll-back-up, occasional skip after long dwell
- **Rate:** stay under the free-tier like cap regardless of what tier you're on

### Budgets — hard limits in code, not conventions
```python
MAX_LIKES_PER_DAY = 8
MAX_ACTIONS_PER_SESSION = 40
MAX_SESSIONS_PER_DAY = 3
MIN_INTER_SESSION_MINUTES = 90
```
Exceeding any budget halts the run. Persist counters to disk so a restart can't reset them.

---

## 10. Phase 5 — Draft generation + approval queue

### Voice grounding
The failure mode here is the recognisable LLM opener: specific-detail-extracted plus
curiosity-question-appended, no actual voice. Median recipient has seen it forty times.

Mitigation: few-shot on 20+ messages **you have actually sent**. Not a description of your
style — the raw messages. The model matches register from examples far better than from
instruction. Include the bad ones; the unevenness is part of the signal.

### Generation contract
- Input: `ProfileRecord` + selected hook + your voice corpus
- Output: 3 candidate openers, each ≤ 2 sentences
- Explicit negative constraints: no "I saw you mentioned X", no question tacked on the
  end by default, no compliment on appearance, no em-dashes if you don't use them

### Approval queue
Simple local UI — a static page or CLI is fine. Shows stitched profile, hook, three
drafts. Actions: pick / edit / rewrite / skip. On approve, the orchestrator navigates to
the composer and types it.

Typing should use `d.send_keys()` with per-character delay, not clipboard paste. Paste is
a distinguishable input event.

---

## 11. Data model

```python
class ProfileRecord(BaseModel):
    id: str                    # hash of first screenshot
    captured_at: datetime
    shots: list[Path]
    dumps: list[Path]
    stitched: Path | None
    prompts: list[Prompt]      # {question, answer, index}
    photos: list[PhotoRef]     # {index, bounds, path}
    attributes: dict[str, str] # age, height, location, job — if in tree
    app_version: str

class ScoredProfile(BaseModel):
    profile_id: str
    score: float
    confidence: float
    hook: Hook
    flags: list[str]
    model: str
    scored_at: datetime

class ActionRecord(BaseModel):
    profile_id: str
    action: Literal["like", "skip", "message"]
    dry_run: bool
    target_element: str | None
    message_text: str | None
    approved_by_human: bool
    at: datetime
```

Everything is append-only. SQLite locally is enough — this is not a 218M-row problem.

---

## 12. Repo layout

```
agent/
  device/
    connection.py      # u2 wrapper, reconnect logic
    actuator.py        # tap/swipe/type + humanisation
    hierarchy.py       # dump parsing, query helpers
  perception/
    capture.py         # Phase 1 harness
    stitch.py
    parse.py           # hierarchy → ProfileRecord
  fsm/
    screens.py         # predicates
    machine.py         # transitions, recovery
    interstitials.py
  judgment/
    scorer.py          # VLM call
    prompts/
  drafting/
    generator.py
    voice_corpus.jsonl
  approval/
    server.py          # local queue UI
  orchestrator.py
  budgets.py
tests/
  corpus/              # Phase 1 captures, committed (or DVC'd)
  test_screens.py      # every dump classifies correctly
  test_stitch.py
  test_scorer.py       # golden outputs
docs/
  adr/                 # decision records
```

---

## 13. Decision records

Keep an ADR per architectural choice — same discipline as STRIDE's CLAUDE.md. Minimum set
to write before Phase 1:

- **ADR-001** Physical device over emulator (rationale: 2.1)
- **ADR-002** Hierarchy owns navigation, VLM owns judgment only (rationale: 3)
- **ADR-003** Phase 0 outcome and resulting perception strategy
- **ADR-004** Human approval gate on all outbound messages (rationale: 2.4)
- **ADR-005** Budget limits and their basis

---

## 14. Testing

| Layer | Method |
|---|---|
| Screen predicates | Replay full corpus, assert exactly one match per dump |
| Stitching | Golden images, SSIM threshold |
| Parsing | Fixture dumps → expected `ProfileRecord` |
| Scorer | Golden JSON on 10 corpus profiles, allow score tolerance |
| Actuator | Dry-run log assertions, no device needed |
| FSM transitions | Mock device returning scripted dumps |
| End-to-end | Manual, supervised, dry-run only |

The corpus is your test fixture set. This is why Phase 1 comes first.

---

## 15. Observability

- Structured JSON logs, one line per action, with `profile_id` correlation
- Screenshot archived on every `UNKNOWN` state and every exception
- Daily summary: profiles seen, scored, liked, skipped, budget consumed, errors by type
- App version pinned and logged — a Hinge update is the most likely cause of sudden
  total breakage, and you want that visible immediately rather than inferred

---

## 16. Failure modes

| Failure | Detection | Response |
|---|---|---|
| App update breaks predicates | `UNKNOWN` rate spikes | Halt, re-run Phase 0, update predicates |
| Rate limit / soft block | Out-of-likes earlier than budget | Halt for 24h |
| Arkose / captcha | New unmatched screen | Halt, manual intervention, do not automate solving |
| ADB disconnect | Connection exception | Retry 3× with backoff, then halt |
| VLM returns malformed JSON | Parse failure | Retry once, then flag and skip profile |
| Scorer drift | Periodic re-calibration vs. your own scoring | Retune prompt |
| Account banned | Login failure | Stop. Project over. See 2.3. |

Halt is always an acceptable response. There is no scenario where guessing beats stopping.

---

## 17. Milestones

| Phase | Deliverable | Est. |
|---|---|---|
| 0 | Feasibility spike + ADR-003 | 1–2 h |
| 1 | Capture harness, 50-profile corpus | 1–2 evenings |
| 2 | State machine, 100% corpus classification | 2–3 evenings |
| 3 | Scorer, rank correlation ≥ 0.7 | 1–2 evenings |
| 4 | Actuator + humanisation, dry-run validated | 1–2 evenings |
| 5 | Drafting + approval queue | 1 evening |
| 6 | Supervised live operation | ongoing |

Total realistic: 2–3 weeks of evenings. Phase 2 will overrun; it always does.

---

## 18. Portability note

Phases 1–4 are app-agnostic. The capture harness, state machine, and offline judgment
layer constitute a general mobile-screen-agent skeleton — the same
perception → state → action shape as the Jarvis MCP work, with a screen instead of a tool
API. If the Hinge-specific layer becomes untenable (2.1, 2.3, or an app update that
flattens the hierarchy), the skeleton survives and retargets.

Design accordingly: keep everything Hinge-specific behind `fsm/screens.py`,
`perception/parse.py`, and the scorer prompt. Nothing else should know what app it's
driving.
