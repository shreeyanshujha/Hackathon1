// Parsing/formatting helpers for the loose text entry used across onboarding.
// Canonical stored forms: times as 'HH:MM' (24h), dates as 'YYYY-MM-DD'.

// Accepts '7', '7:30', '7.30', '7:30 pm', '19:00' → 'HH:MM' or null.
export function parseTime(input) {
  if (!input) return null;
  const s = String(input).trim().toLowerCase();
  const m = s.match(/^(\d{1,2})(?:[:.](\d{1,2}))?\s*(am|pm|a|p)?\.?$/);
  if (!m) return null;
  let h = parseInt(m[1], 10);
  const min = m[2] ? parseInt(m[2], 10) : 0;
  const mer = m[3];
  if (min > 59) return null;
  if (mer) {
    if (h < 1 || h > 12) return null;
    if ((mer === 'pm' || mer === 'p') && h !== 12) h += 12;
    if ((mer === 'am' || mer === 'a') && h === 12) h = 0;
  } else if (h > 23) {
    return null;
  }
  return `${String(h).padStart(2, '0')}:${String(min).padStart(2, '0')}`;
}

export function formatTime(hhmm) {
  if (!hhmm) return '';
  const [h, m] = hhmm.split(':').map(Number);
  const mer = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, '0')} ${mer}`;
}

// Accepts 'DD/MM/YYYY' with /, -, . or space separators → 'YYYY-MM-DD' or null.
export function parseDob(input) {
  if (!input) return null;
  const m = String(input).trim().match(/^(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{4})$/);
  if (!m) return null;
  const d = +m[1];
  const mo = +m[2];
  const y = +m[3];
  const date = new Date(Date.UTC(y, mo - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return null;
  if (y < 1900 || date.getTime() > Date.now()) return null;
  return `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

export function formatDob(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}/${m}/${y}`;
}

export function ageFromDob(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  const now = new Date();
  let age = now.getFullYear() - y;
  if (now.getMonth() + 1 < m || (now.getMonth() + 1 === m && now.getDate() < d)) age -= 1;
  return age;
}

// Strips spacing/punctuation; accepts local (0412 345 678) or international (+61…) forms.
export function normalizePhone(input) {
  if (!input) return null;
  const s = String(input).replace(/[\s().-]/g, '');
  if (!/^\+?\d{8,15}$/.test(s)) return null;
  return s;
}
