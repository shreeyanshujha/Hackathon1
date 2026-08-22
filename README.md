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
  main.py               FastAPI: /ingest /state /simulate /voice/* + dashboard
  engine.py             rule engine + cooldown state machine + watchdog logic
  telephony.py          Twilio call + TwiML + SMS (auto-simulated w/o creds)
  config.yaml           every threshold (demo-compressed values)
  profiles.json         4 demo users, one per scenario
  static/dashboard.html live dashboard with one-click scenario buttons
```

### 2.1 Run the backend (no accounts, no logins needed)

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — that's the projector dashboard. The buttons
along the top fire every scenario; without Twilio credentials calls/SMS are
*simulated* and show up in the event log, so the whole demo works offline.

Tests (12 cases: all scenarios, cooldown, watchdog, DTMF, wording):

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

### 2.3 Run it on the watch (Versa 2)

The only login in this module is Fitbit's one-time developer-bridge sign-in —
it is the only way any code gets onto a physical Fitbit.

1. Edit the two constants at the top of `hackathon-app/companion/index.js`:
   `BASE_URL` (your tunnel URL) and `USER_ID` (which demo profile the watch
   streams as, default `usr_demo_a`).
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
   peerSocket link is up. Batches hit `/ingest` every 5 s and Jeff's card on
   the dashboard goes live.

### 2.4 Demo runbook (four scenarios, ~6 minutes)

Projector shows the dashboard. Thresholds are demo-compressed in
`backend/config.yaml` (acute 15 s, immobility 30 s, missing data 2 min,
cooldown 60 min) — production values are noted inline there.

| # | Scenario | Button / command | What the room sees |
|---|----------|------------------|--------------------|
| 1 | Normal routine | "Normal activity (Jeff)" | HR 107+, moving → card stays green, log-only line: "consistent with activity, no action". Establishes that we don't cry wolf. |
| 2 | Acute anomaly | "Acute (Jeff)" | HR climbs to 142 while stationary; card turns amber ("evaluating"), then red ALERT after 15 s of sustained data → kin phone rings. Press **1** live on speakerphone: responder phone rings (bridged) + SMS arrives. |
| 3 | Prolonged immobility | "Immobility (Margaret)" | Near-zero movement in waking hours → low-urgency script. Press **2**: stand-down, logged as false positive, cooldown shown on card. |
| 4 | Wandering | "Wandering (Elsie)" | Elsie's `sleep_window` in `profiles.json` is deliberately 00:00–23:59 so "night" overlaps demo time honestly — sustained movement inside her sleep window → schedule-relative alert. |
| 5 | Lost connection | "Lost connection (Harold)" then wait ~2–3 min | Seeds three readings, then silence. The watchdog (60 s sweep) notices `missing_data_min` exceeded → low-urgency "lost connection with Harold's watch" call. |

Between repeat runs of the same scenario: "Reset all cooldowns" button (or
`POST /demo/reset-cooldown/{user_id}`) — one incident = one call is enforced
per user, which is also why each scenario has its own demo user.

### Notes

- **Wording**: escalation is called *Escalate & Notify* everywhere; a test
  (`test_no_emergency_dispatch_language`) fails the build if dispatch-style
  language sneaks into code, UI, or scripts.
- **Voice**: Twilio built-in TTS (`<Say>`). ElevenLabs is a clearly marked
  v2 stub in `telephony.py` (`elevenlabs_tts_url`).
- **State**: everything in-memory (ring buffers + dicts). Restarting uvicorn
  resets the world — a feature during demos.
- The telemetry schema is identical for the watch pipeline and the
  simulator, so everything demoed via buttons is exactly what the watch
  path exercises.
