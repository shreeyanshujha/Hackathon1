// Module 1 -> Module 2 handoff: POST the saved UserBaselineProfile to the
// backend, which applies it to the live watch card (POST /profile).

import { BACKEND_URL, API_KEY } from '../config';

export async function syncProfileToBackend(profile) {
  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) headers['X-Api-Key'] = API_KEY;

  const res = await fetch(`${BACKEND_URL}/profile`, {
    method: 'POST',
    headers,
    body: JSON.stringify(profile),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || `sync failed (HTTP ${res.status})`);
  }
  return body;
}
