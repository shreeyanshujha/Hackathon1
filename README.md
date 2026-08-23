# Hackathon1 — Care Companion

First Hackathon experience — Hack the Gong 2026, aged care challenge track.

Companion system for a wearable that watches for signs of risk in elderly people
living alone. When an anomaly is detected, an automated agent places a real call
to the next of kin with an interactive **Escalate & Notify / Stand down**
decision tree. Escalation always means bridging the kin to a pre-configured
**human responder number** plus a vitals SMS — nothing in the prototype dials,
or claims to dial, any public urgent-help line.

Two modules live in this repo:

| Module | Where | What |
| --- | --- | --- |
| 1. Onboarding & baseline profile | repo root (`App.js`, `src/`) | Expo app that produces the personalized `UserBaselineProfile` |
| 2. Anomaly engine, telephony & watch | `backend/`, `hackathon-app/` | Fitbit Versa 2 → FastAPI rule engine → Twilio voice/SMS + live dashboard |

---

## Module 1 — Onboarding & baseline profile (Expo app)

A universal threshold ("no movement for 20 min = alert") can't work: normal varies per
person — the afternoon napper, the 10 am sleeper, someone with limited mobility. This
module produces the **personalized baseline** the anomaly engine compares live
sensor data against.

Multi-step onboarding flow (welcome → demographics → emergency contacts → sleep →
weekly routine → hobbies & mobility → lifestyle → health context → consent → review)
that outputs a validated `UserBaselineProfile`, persisted locally. Partial progress
auto-saves, so a half-finished setup resumes after an app restart. A saved profile can
be re-opened and edited.

### Run it

```bash
npm install
npx expo start
```

Scan the QR code with the Expo Go app (iOS/Android). Everything used here (AsyncStorage,
safe-area) runs inside Expo Go — no native build needed.

### The data contract

Later modules read this object and nothing else:

```js
UserBaselineProfile {
  schemaVersion: 1,
  demographics: { name, sex, dob: 'YYYY-MM-DD', livingSituation },
  emergencyContacts: [{ name, relationship, phone, isPrimary }],  // primary first
  sleep: { typicalWake: 'HH:MM', typicalSleep: 'HH:MM', napPattern: string|null },
  weeklyRoutine: {
    monday: [{ activity, expectedTime: 'HH:MM', expectedDuration: minutes }],
    // … tuesday–sunday
  },
  hobbies: [string],
  mobilityLevel: 'fully_mobile' | 'walking_aid' | 'limited_mobility',
  lifestyle: { diet, smoking: { status, frequency }, alcohol: { status, frequency } },
  healthContext: [string],   // self-reported, advisory only — never diagnostic
  medicationCount: number | null,
  consent: { monitoringConsent: bool, sharedWith: ['nextOfKin' | 'carer' | 'gp'] },
  deviceId: null,            // reserved for wearable pairing
  completedAt: ISO datetime
}
```

The completed profile is visible in-app: **View raw profile JSON** on the home screen.

### Where things live

| Path | What it is |
| --- | --- |
| `src/model/profile.js` | Schema, option enums, validation, draft ⇄ profile mapping. Pure data logic — no UI, no sensors. |
| `src/storage/profileStore.js` | Persistence (AsyncStorage). Later modules call `loadProfile()`; swapping in Firebase/Supabase touches only this file. |
| `src/onboarding/OnboardingFlow.js` | Step controller: progress, validation, autosave, final build-and-save. |
| `src/onboarding/steps/` | One screen per onboarding section. |
| `src/screens/ProfileHomeScreen.js` | Post-setup summary + raw contract JSON viewer. |
| `src/components/ui.js` | Shared form controls (chips, tag editor, time fields…). |
| `onboarding questions/` | Original question brainstorm this flow was built from. |

Health context is **self-reported and advisory only**: it gives the calling agent
useful context ("they do have a heart condition"). The device never claims to detect
or diagnose conditions. Consent to monitoring is required to finish onboarding; data
visibility (next of kin / carer / GP) is chosen by the user.

---

## Module 2 — Anomaly engine, telephony & watch

A Fitbit Versa 2 streams heart rate + motion to a FastAPI backend that
evaluates a discrepancy matrix (acute distress, prolonged immobility,
wandering, lost connection) and places the Twilio voice call with the
escalate/stand-down DTMF tree. In this pass the engine reads demo profiles
from `backend/profiles.json`; wiring it to Module 1's `UserBaselineProfile`
contract is the integration step (the fields map 1:1 — sleep window, weekly
routine, contacts).

```
hackathon-app/          Fitbit Versa 2 app (SDK 4.2 / OS 4) + companion
backend/
  main.py               FastAPI: /ingest /state /simulate /fitbit/* /voice/* + dashboard
  engine.py             rule engine + cooldown state machine + watchdog logic
  fitbit.py             Fitbit Web API bridge (OAuth PKCE + intraday HR poller)
  telephony.py          Twilio call + TwiML + SMS (auto-simulated w/o creds)
  config.yaml           every threshold (demo-compressed values)
  profiles.json         3 simulated users + 1 live card fed by the real watch
  static/dashboard.html live dashboard with one-click scenario buttons
```

The dashboard shows four cards: **Jeff, Margaret and Harold stream simulated
telemetry continuously** (tagged SIM — believable HR/steps at any hour, asleep
inside their sleep windows, never tripping a rule on their own), and the
**live card (tagged LIVE · FITBIT) is the real watch** — fed either by the
Fitbit Web API poller (§2.3) or the on-watch developer-bridge app (§2.4).

### 2.1 Run the backend (no accounts, no logins needed)

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload \
  --reload-include ".env" --reload-include "profiles.json" --reload-include "config.yaml"
```

Open **http://localhost:8000** — that's the projector dashboard. The three SIM
cards go live within ~5 s. The buttons along the top fire every scenario
(each pauses that user's simulated stream briefly so the alert stays visible);
without Twilio credentials calls/SMS are *simulated* and show up in the event
log, so the whole demo works offline. `--reload` picks up backend edits;
`dashboard.html` edits just need a browser refresh.

Tests (17 cases: scenarios, cooldown, watchdog, DTMF, TriggerEvent contract,
mock-stream safety, wording):

```bash
cd backend && .venv/bin/pytest test_scenarios.py -v
```

Or by hand:

```bash
curl -X POST "localhost:8000/simulate?scenario=acute"        # -> ALERT + call
curl -X POST "localhost:8000/simulate?scenario=acute"        # -> suppressed (cooldown)
curl -X POST "localhost:8000/demo/reset-cooldown/usr_demo_a" # -> clear it
curl localhost:8000/state | python3 -m json.tool
```

### 2.2 Real phone calls (optional, ~5 min)

1. `cp backend/.env.example backend/.env` and fill in the Twilio SID, auth
   token, and From number, plus your real `KIN_NUMBER` and `RESPONDER_NUMBER`
   (these override the placeholder numbers in `profiles.json`).
2. Twilio must be able to reach the TwiML webhooks, so start a tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:8000   # or: ngrok http 8000
   ```
   Put the printed HTTPS URL into `.env` as `PUBLIC_BASE_URL` (no trailing
   slash) and restart uvicorn.
3. Fire a scenario. The kin phone rings, hears the scenario script, then:
   - **1 = Escalate & Notify** → bridged to the responder number + SMS with
     vitals lands on the kin phone.
   - **2 = Stand down** → verbal confirmation, logged as false positive,
     cooldown stays armed.

### 2.3 (recommended first) Live card via the Fitbit Web API

Proves the pipeline is real without flashing any code onto the watch: the
backend polls Fitbit's cloud (Google's wearable API surface — sign in with the
Google account the watch is paired to) for intraday heart rate + steps and
pushes them through the same `/ingest` pipeline.

1. Register an app once at <https://dev.fitbit.com/apps/new>:
   **OAuth 2.0 Application Type = Personal** (that's what unlocks intraday HR
   for your own account), **Callback URL = `http://localhost:8000/fitbit/callback`**.
2. Put the Client ID in `backend/.env` as `FITBIT_CLIENT_ID=…` (see
   `.env.example`; no secret needed — PKCE).
3. On the dashboard, the live card shows **Connect watch →** — click it, sign
   in, approve. Done: the poller runs every 60 s, the card fills with real HR,
   and `resting_hr_bpm` auto-calibrates from your Fitbit profile.

Freshness caveat: data reaches Fitbit's cloud only when the watch syncs with
the phone, so expect minute-level lag (keep the Fitbit app open in your pocket
to sync often). The live card's lost-connection threshold is 15 min
(`missing_data_min` override in `profiles.json`) to allow for that. For true
second-by-second streaming use the developer bridge below.

### 2.4 Run it on the watch (Versa 2, second-by-second)

The only login in this path is Fitbit's one-time developer-bridge sign-in —
it is the only way any code gets onto a physical Fitbit.

1. Edit the two constants at the top of `hackathon-app/companion/index.js`:
   `BASE_URL` (your tunnel URL) and `USER_ID` (which profile the watch
   streams as, default `usr_live` — the live card).
2. On the **watch**: Settings → Developer Bridge → wait for "Connected".
   (Watch needs Wi-Fi; keep it on the charger so Wi-Fi stays up.)
3. On the **phone**: Fitbit app → your account → Developer Menu → enable
   Developer Bridge (the companion JS runs inside the Fitbit phone app).
4. On this machine:
   ```bash
   cd hackathon-app
   npx fitbit          # opens the Fitbit CLI shell, browser sign-in once
   fitbit$ connect device
   fitbit$ connect phone
   fitbit$ install     # builds + flashes app and companion
   ```
   (`npx fitbit-build` alone compiles without a watch — CI-style check.)
5. The watch face shows live HR, motion state, and a green dot when the
   peerSocket link is up. Batches hit `/ingest` every 5 s and the live card
   on the dashboard updates in real time.

### 2.5 Module 1 → Module 2: sync the onboarded baseline

The Expo app's finished `UserBaselineProfile` drives the live card. On the
post-setup home screen, tap **Send to watch service** (set `BACKEND_URL` —
your computer's LAN IP or tunnel URL — and, if used, `API_KEY` in
`src/config.js` first). Under the hood this is `POST /profile`:

- The **live card takes on the onboarded person**: name, age (from dob),
  sleep window (`typicalSleep`→`typicalWake`), weekly routine, and the
  primary emergency contact as kin (a second contact becomes the responder
  number; the `.env` overrides still win).
- What onboarding can't know is preserved: `source: fitbit`, the calibrated
  `resting_hr_bpm`, and the cloud-lag threshold overrides.
- **Consent is a hard gate** — a profile without `monitoringConsent` is
  refused with 403, never silently accepted.
- The merge persists to the gitignored `backend/profiles.local.json`
  overlay, so a backend restart keeps the person (delete the file to get
  the stock demo card back).

### 2.6 Module 2 → escalation agents: the ladder takes the calls

The `escalation-ladder` branch is the team's agent system for everything
*after* detection (Agent A calls the wearer, Agent B the kin list, then the
support fallback — see its own README). It stays on its branch; run it from a
worktree so `main` keeps this layout:

```bash
git worktree add escalation-service escalation-ladder   # once (gitignored)
cd escalation-service
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp ../hackathon-app/secrets .env      # or .env.example for the zero-cred demo
.venv/bin/python -m uvicorn escalation.api:app --port 8100
```

With `ESCALATION_URL=http://localhost:8100` in `backend/.env`, every fired
TriggerEvent is translated (`backend/escalation_bridge.py`) into the ladder's
`POST /alerts` contract and the agents own the calls — the dashboard logs the
handoff, and http://localhost:8100 shows the ladder's live transition log.
Urgency maps to tier (high→3, med→2, low→1); kin and responder numbers ride
along from the profile. If the service is down or `ESCALATION_URL` unset, the
legacy §2.2 Twilio DTMF call happens instead — a dead sidecar never swallows
an alert. Real agent voice calls need `CALL_PROVIDER=elevenlabs` plus
`TWILIO_FROM_NUMBER` and real numbers in `escalation-service/.env`; the
default `dryrun` runs the whole ladder offline.

### 2.7 Demo runbook (four scenarios, ~6 minutes)

Projector shows the dashboard. Thresholds are demo-compressed in
`backend/config.yaml` (acute 15 s, immobility 30 s, missing data 2 min,
cooldown 60 min) — production values are noted inline there.

| # | Scenario | Button / command | What the room sees |
|---|----------|------------------|--------------------|
| 1 | Normal routine | "Normal activity (Jeff)" | HR 107+, moving → card stays green, log-only line: "consistent with activity, no action". Establishes that we don't cry wolf. |
| 2 | Acute anomaly | "Acute (Jeff)" | HR climbs to 142 while stationary; card turns amber ("evaluating"), then red ALERT after 15 s of sustained data → kin phone rings. Press **1** live on speakerphone: responder phone rings (bridged) + SMS arrives. |
| 3 | Prolonged immobility | "Immobility (Margaret)" | Near-zero movement in waking hours → low-urgency script. Press **2**: stand-down, logged as false positive, cooldown shown on card. |
| 4 | Wandering | "Wandering (Harold)" | The sequence is stamped inside Harold's real 23:00–06:00 sleep window (the engine sees sustained movement "last night at 11 pm") → schedule-relative alert, honestly, at any demo time. |
| 5 | Lost connection | "Lost connection (Harold)" then wait ~2–3 min | Seeds three readings, then his simulated stream pauses itself. The watchdog (60 s sweep) notices `missing_data_min` exceeded → low-urgency "lost connection with Harold's watch" call — then the stream resumes and the card logs "telemetry resumed". |

Between repeat runs of the same scenario: "Reset all cooldowns" button (or
`POST /demo/reset-cooldown/{user_id}`) — one incident = one call is enforced
per user, which is also why each scenario has its own demo user.

### Notes

- **Tunnel security**: whenever `PUBLIC_BASE_URL` points at a live tunnel, set
  `API_SHARED_SECRET` in `backend/.env` (any random string). `/ingest`,
  `/simulate`, `/state` and `/demo/*` then require it as an `X-Api-Key`
  header: open the dashboard as `http://localhost:8000/?key=THE_SECRET` and
  set the same value as `API_KEY` in `hackathon-app/companion/index.js`.
  The `/voice/*` webhooks verify Twilio's request signature automatically
  once `TWILIO_AUTH_TOKEN` is set. Unset both = the zero-config open demo.
- **Wording**: escalation is called *Escalate & Notify* everywhere; a test
  (`test_no_emergency_dispatch_language`) fails the build if dispatch-style
  language sneaks into code, UI, or scripts.
- **Voice**: Twilio built-in TTS (`<Say>`). ElevenLabs is a clearly marked
  v2 stub in `telephony.py` (`elevenlabs_tts_url`).
- **State**: everything in-memory (ring buffers + dicts). Restarting uvicorn
  resets the world — a feature during demos. (Fitbit OAuth tokens survive in
  `backend/.fitbit_tokens.json`, gitignored, so the live card reconnects by
  itself.)
- The telemetry schema is identical for the watch pipeline and the
  simulator, so everything demoed via buttons is exactly what the watch
  path exercises.
