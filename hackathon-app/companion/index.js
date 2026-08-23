/* Telecare companion: receives device batches over peerSocket and POSTs them
 * to the backend /ingest endpoint. Fire-and-forget with one bounded retry —
 * we never queue unbounded data.
 */

import { peerSocket } from "messaging";

// ======================= CONFIGURE ME =======================
// Public HTTPS URL of the backend (cloudflared / ngrok tunnel), no trailing /.
const BASE_URL = "https://trustees-corresponding-ideas-share.trycloudflare.com";
// Which profile this physical watch streams as (see backend/profiles.json).
// usr_live is the live card; the usr_demo_* cards stream mock data on their own.
const USER_ID = "usr_live";
// Must match API_SHARED_SECRET in backend/.env; leave "" if the backend
// runs without one.
const API_KEY = "";
// ============================================================

const RETRY_DELAY_MS = 3000;
const STEP_WINDOW_MS = 5 * 60 * 1000;

// Rolling 5-minute step window built from device step deltas.
let stepEvents = []; // [{ts, delta}]

function stepsLast5Min(now) {
  stepEvents = stepEvents.filter((e) => now - e.ts < STEP_WINDOW_MS);
  return stepEvents.reduce((sum, e) => sum + e.delta, 0);
}

function postReading(payload, retriesLeft) {
  const headers = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-Api-Key"] = API_KEY;
  fetch(`${BASE_URL}/ingest`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  })
    .then((res) => {
      if (!res.ok) throw new Error(`ingest ${res.status}`);
    })
    .catch((err) => {
      if (retriesLeft > 0) {
        setTimeout(() => postReading(payload, retriesLeft - 1), RETRY_DELAY_MS);
      } else {
        console.log(`ingest dropped: ${err}`);
      }
    });
}

peerSocket.addEventListener("message", (evt) => {
  const m = evt.data || {};
  const now = Date.now();
  if (m.steps_delta > 0) stepEvents.push({ ts: now, delta: m.steps_delta });

  postReading(
    {
      user_id: USER_ID,
      timestamp: new Date(now).toISOString(),
      // hr 0 means the sensor had no reading (off-wrist); the contract is
      // null so the engine treats it as unknown, not a real low HR.
      heart_rate_bpm: m.hr > 0 ? m.hr : null,
      step_count_last_5min: stepsLast5Min(now),
      motion_intensity: m.motion === "moving" ? "moving" : "stationary",
      battery_pct: m.battery || 100,
    },
    1 // one retry, then drop
  );
});

peerSocket.addEventListener("open", () => console.log("companion: socket open"));
peerSocket.addEventListener("error", (e) => console.log(`companion: ${e.code}`));
