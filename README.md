# Telecare Watch — AI anomaly detection for elderly users

Hackathon prototype: a Fitbit Versa 2 streams heart rate + motion to a
FastAPI backend that evaluates a discrepancy matrix (acute distress,
prolonged immobility, wandering, lost connection) and places an automated
Twilio voice call to next-of-kin with an interactive **Escalate & Notify /
Stand down** decision tree.

> This system never dials or claims to dial any public urgent-help line.
> Escalation always means: bridge the kin to a pre-configured **human
> responder number** and SMS the kin a vitals summary.

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

## 1. Run the backend (no accounts, no logins needed)

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

## 2. Real phone calls (optional, ~5 min)

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

## 3. Run it on the watch (Versa 2)

The only login in the entire project is Fitbit's one-time developer-bridge
sign-in — it is the only way any code gets onto a physical Fitbit; there is
no account anywhere else in the stack.

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

## 4. Demo runbook (four scenarios, ~6 minutes)

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

## Notes

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
