"""Fitbit Web API bridge — feeds the live user's card with real watch data.

Fitbit is Google's wearable platform and this Web API is the supported way to
read watch data from a server (the old Google Fit REST API is retired; Health
Connect is on-device Android only). Sign-in is the account's Google/Fitbit
login via OAuth2 + PKCE.

Flow:
  1. Register an app once at https://dev.fitbit.com/apps/new
       OAuth 2.0 Application Type: Personal  (grants intraday HR for your own account)
       Callback URL: http://localhost:8000/fitbit/callback
  2. Put the Client ID in backend/.env as FITBIT_CLIENT_ID.
  3. Click "Connect watch" on the live card (or open /fitbit/login).

The poller then pulls intraday heart rate (1-sec detail) and per-minute steps,
converts them to the same Telemetry shape the wearable posts, and pushes them
through the normal engine.ingest() pipeline — the rule engine cannot tell the
difference between this and the on-watch streamer.

Reality check on freshness: samples only reach Fitbit's cloud when the watch
syncs with the phone app, so expect minute-level latency (keep the Fitbit app
open to sync often). The developer-bridge watch app in hackathon-app/ remains
the second-by-second live path.

Rate limit is 150 requests/hour per user: 2 calls per poll at the default
60-second interval (plus a battery check every 10th poll) stays well under it.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / ".fitbit_tokens.json"

AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"
TOKEN_URL = "https://api.fitbit.com/oauth2/token"
API = "https://api.fitbit.com"
SCOPES = "heartrate activity settings"

LOOKBACK_MIN = 15          # intraday window fetched each poll
MIN_SAMPLE_SPACING_SEC = 15
BATTERY_EVERY_N_POLLS = 10


def env(name, default=""):
    return os.environ.get(name, default).strip()


class FitbitBridge:
    def __init__(self, user_id, ingest_fn, log_event, set_resting_hr):
        self.user_id = user_id          # engine user the watch streams as
        self.ingest_fn = ingest_fn
        self.log_event = log_event
        self.set_resting_hr = set_resting_hr
        self._tokens = self._load_tokens()
        self._verifier = None
        self._oauth_state = None
        self._last_sample_dt = None
        self._last_sync = None
        self._last_error = None
        self._battery_pct = 100
        self._poll_count = 0
        self._announced = bool(self._tokens)

    # ------------------------------------------------------------- token store
    def _load_tokens(self):
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _save_tokens(self, payload):
        payload["obtained_at"] = datetime.now().isoformat(timespec="seconds")
        self._tokens = payload
        TOKEN_FILE.write_text(json.dumps(payload, indent=2))
        TOKEN_FILE.chmod(0o600)

    def _drop_tokens(self):
        self._tokens = {}
        TOKEN_FILE.unlink(missing_ok=True)

    # ------------------------------------------------------------------ config
    def configured(self):
        return bool(env("FITBIT_CLIENT_ID"))

    def authorized(self):
        return bool(self._tokens.get("refresh_token"))

    def poll_sec(self):
        return max(30, int(env("FITBIT_POLL_SEC", "60") or 60))

    def redirect_uri(self):
        return env("FITBIT_REDIRECT_URI") or "http://localhost:8000/fitbit/callback"

    def _token_headers(self):
        secret = env("FITBIT_CLIENT_SECRET")
        if not secret:
            return {}  # public client: PKCE only, client_id goes in the body
        basic = base64.b64encode(
            ("%s:%s" % (env("FITBIT_CLIENT_ID"), secret)).encode()).decode()
        return {"Authorization": "Basic " + basic}

    # ------------------------------------------------------------------- oauth
    def login_url(self):
        self._verifier = secrets.token_urlsafe(96)[:128]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self._verifier.encode()).digest()).rstrip(b"=").decode()
        self._oauth_state = secrets.token_urlsafe(16)
        return AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code",
            "client_id": env("FITBIT_CLIENT_ID"),
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": self._oauth_state,
            "redirect_uri": self.redirect_uri(),
        })

    def exchange_code(self, code, state):
        if not self._verifier or state != self._oauth_state:
            raise ValueError("login flow was restarted — open /fitbit/login again")
        r = httpx.post(TOKEN_URL, headers=self._token_headers(), data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": env("FITBIT_CLIENT_ID"),
            "redirect_uri": self.redirect_uri(),
            "code_verifier": self._verifier,
        }, timeout=20)
        if r.status_code != 200:
            raise RuntimeError("Fitbit token exchange failed (%s): %s"
                               % (r.status_code, r.text[:300]))
        self._save_tokens(r.json())
        self._last_error = None
        self.log_event(self.user_id or "system", "fitbit_connected",
                       "Fitbit account connected — live watch data will stream "
                       "via the Fitbit Web API.")
        self._announced = True

    async def _refresh(self, client):
        r = await client.post(TOKEN_URL, headers=self._token_headers(), data={
            "grant_type": "refresh_token",
            "refresh_token": self._tokens.get("refresh_token", ""),
            "client_id": env("FITBIT_CLIENT_ID"),
        })
        if r.status_code != 200:
            self._drop_tokens()
            self.log_event(self.user_id or "system", "fitbit_disconnected",
                           "Fitbit token refresh failed — reconnect from the "
                           "dashboard (/fitbit/login).")
            return False
        self._save_tokens(r.json())
        return True

    async def _get(self, client, url):
        headers = {"Authorization": "Bearer " + self._tokens.get("access_token", "")}
        r = await client.get(url, headers=headers)
        if r.status_code == 401 and await self._refresh(client):
            headers = {"Authorization": "Bearer " + self._tokens["access_token"]}
            r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()

    # ----------------------------------------------------------------- polling
    async def _poll_once(self, client):
        # Refresh proactively when the access token is close to expiring.
        obtained = self._tokens.get("obtained_at")
        expires = int(self._tokens.get("expires_in", 28800))
        if obtained and datetime.now() > (
                datetime.fromisoformat(obtained) + timedelta(seconds=expires - 300)):
            if not await self._refresh(client):
                return

        now = datetime.now()
        start = now - timedelta(minutes=LOOKBACK_MIN)
        if start.date() != now.date():
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        t0, t1 = start.strftime("%H:%M"), now.strftime("%H:%M")

        hr_body = await self._get(
            client, "%s/1/user/-/activities/heart/date/today/1d/1sec/time/%s/%s.json"
            % (API, t0, t1))
        steps_body = await self._get(
            client, "%s/1/user/-/activities/steps/date/today/1d/1min/time/%s/%s.json"
            % (API, t0, t1))

        self._poll_count += 1
        if self._poll_count % BATTERY_EVERY_N_POLLS == 1:
            try:
                devices = await self._get(client, API + "/1/user/-/devices.json")
                if devices:
                    self._battery_pct = int(devices[0].get("batteryLevel",
                                                           self._battery_pct))
            except Exception:
                pass  # battery is cosmetic; never fail the poll over it

        # Fitbit reports the wearer's true resting HR — calibrate the profile.
        summary = (hr_body.get("activities-heart") or [{}])[0].get("value", {})
        resting = summary.get("restingHeartRate")
        if resting:
            self.set_resting_hr(int(resting))

        steps_by_minute = {
            e["time"][:5]: int(e["value"])
            for e in (steps_body.get("activities-steps-intraday", {})
                      .get("dataset", []))}

        def steps_last_5min(dt):
            return sum(steps_by_minute.get((dt - timedelta(minutes=k)).strftime("%H:%M"), 0)
                       for k in range(5))

        ingested = 0
        last_kept = None
        for entry in hr_body.get("activities-heart-intraday", {}).get("dataset", []):
            h, m, s = (int(x) for x in entry["time"].split(":"))
            dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
            if dt > now:  # time strings are today's; guard clock skew
                continue
            if self._last_sample_dt and dt <= self._last_sample_dt:
                continue
            if last_kept and (dt - last_kept).total_seconds() < MIN_SAMPLE_SPACING_SEC:
                continue
            s5 = steps_last_5min(dt)
            motion = "high" if s5 >= 120 else "moderate" if s5 >= 25 else "stationary"
            self.ingest_fn({
                "user_id": self.user_id,
                "timestamp": dt.isoformat(timespec="seconds"),
                "heart_rate_bpm": int(entry["value"]),
                "step_count_last_5min": s5,
                "motion_intensity": motion,
                "battery_pct": self._battery_pct,
            })
            last_kept = dt
            self._last_sample_dt = dt
            ingested += 1

        self._last_sync = datetime.now()
        if ingested:
            self._last_error = None

    async def run(self):
        """Poll loop task; created by main.py's lifespan."""
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                delay = 15
                if self.user_id and self.configured() and self.authorized():
                    try:
                        await self._poll_once(client)
                        delay = self.poll_sec()
                    except Exception as exc:
                        message = "%s: %s" % (type(exc).__name__, exc)
                        if message != self._last_error:
                            self._last_error = message
                            self.log_event(self.user_id, "fitbit_error",
                                           "Fitbit poll failed — %s" % message)
                        delay = self.poll_sec()
                await asyncio.sleep(delay)

    # ------------------------------------------------------------------ status
    def status(self):
        sync_ago = (int((datetime.now() - self._last_sync).total_seconds())
                    if self._last_sync else None)
        return {
            "configured": self.configured(),
            "authorized": self.authorized(),
            "live_user": self.user_id,
            "poll_sec": self.poll_sec(),
            "last_sync_sec_ago": sync_ago,
            "last_sample_ts": (self._last_sample_dt.isoformat(timespec="seconds")
                               if self._last_sample_dt else None),
            "last_error": self._last_error,
            "login_path": "/fitbit/login",
        }

    def setup_help_html(self):
        return """
        <body style="font-family:system-ui;background:#0e1420;color:#e8eef7;padding:40px;line-height:1.7">
        <h2>Fitbit is not configured yet</h2>
        <ol>
          <li>Register an app at <a style="color:#60a5fa" href="https://dev.fitbit.com/apps/new">dev.fitbit.com/apps/new</a>
              (sign in with the Google account the watch is paired to).</li>
          <li>Set <b>OAuth 2.0 Application Type</b> to <b>Personal</b> and
              <b>Callback URL</b> to <code>%s</code>.</li>
          <li>Copy the Client ID into <code>backend/.env</code> as
              <code>FITBIT_CLIENT_ID=…</code> (see .env.example).</li>
          <li>Come back to <a style="color:#60a5fa" href="/fitbit/login">/fitbit/login</a>.</li>
        </ol>
        </body>""" % self.redirect_uri()
