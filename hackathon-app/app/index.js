/* Telecare device app (Versa 2 / Fitbit OS 4, SDK 4.2).
 *
 * Samples HR + accelerometer every second, derives a coarse motion state
 * from accelerometer magnitude variance, and ships a tiny JSON batch to the
 * companion over peerSocket every 5 seconds.
 *
 * Kept deliberately ES5-ish: the device runtime is JerryScript with a tiny
 * memory budget.
 */

import document from "document";
import { HeartRateSensor } from "heart-rate";
import { Accelerometer } from "accelerometer";
import { today } from "user-activity";
import { battery } from "power";
import { peerSocket } from "messaging";

var SEND_INTERVAL_MS = 5000;
var MOTION_VARIANCE_THRESHOLD = 0.6; // (m/s^2)^2 on magnitude; tune on-wrist

var hrText = document.getElementById("hr-text");
var motionText = document.getElementById("motion-text");
var connDot = document.getElementById("conn-dot");

var lastHr = 0;
var magnitudes = []; // accel magnitude samples since last flush
var lastSteps = (today.adjusted && today.adjusted.steps) || 0;

// ---- sensors ---------------------------------------------------------------
var hrm = null;
if (HeartRateSensor) {
  hrm = new HeartRateSensor({ frequency: 1 });
  hrm.addEventListener("reading", function () {
    lastHr = hrm.heartRate || 0;
    hrText.text = lastHr > 0 ? String(lastHr) : "--";
  });
  hrm.start();
} else {
  hrText.text = "n/a";
}

var accel = null;
if (Accelerometer) {
  accel = new Accelerometer({ frequency: 5 });
  accel.addEventListener("reading", function () {
    var x = accel.x || 0, y = accel.y || 0, z = accel.z || 0;
    magnitudes.push(Math.sqrt(x * x + y * y + z * z));
    if (magnitudes.length > 40) magnitudes.shift(); // hard memory cap
  });
  accel.start();
}

// ---- motion from magnitude variance ---------------------------------------
function motionState() {
  var n = magnitudes.length;
  if (n < 5) return "stationary";
  var mean = 0, i;
  for (i = 0; i < n; i++) mean += magnitudes[i];
  mean /= n;
  var variance = 0;
  for (i = 0; i < n; i++) {
    var d = magnitudes[i] - mean;
    variance += d * d;
  }
  variance /= n;
  return variance > MOTION_VARIANCE_THRESHOLD ? "moving" : "stationary";
}

// ---- connection state ------------------------------------------------------
function paintConn() {
  connDot.style.fill =
    peerSocket.readyState === peerSocket.OPEN ? "#34d399" : "#f87171";
}
peerSocket.addEventListener("open", paintConn);
// The device cannot manually reopen a peerSocket; the OS re-establishes it.
// We just repaint the dot and keep batching — sends resume once it reopens.
peerSocket.addEventListener("close", paintConn);
peerSocket.addEventListener("error", paintConn);
paintConn();

// ---- 5s flush --------------------------------------------------------------
setInterval(function () {
  var motion = motionState();
  motionText.text = motion;

  var stepsNow = (today.adjusted && today.adjusted.steps) || 0;
  var delta = stepsNow - lastSteps;
  if (delta < 0) delta = 0; // day rollover
  lastSteps = stepsNow;

  magnitudes = [];

  if (peerSocket.readyState !== peerSocket.OPEN) return;
  try {
    // Tiny payload: well under peerSocket's per-message buffer limit.
    peerSocket.send({
      hr: lastHr,
      steps_delta: delta,
      motion: motion,
      battery: Math.round(battery.chargeLevel || 0),
      ts: Date.now()
    });
  } catch (e) {
    // Buffer full or socket dropped mid-send: skip this batch, never queue.
  }
}, SEND_INTERVAL_MS);
