// Where the kiosk shell's window may go. It shows the page the launcher opened
// plus the origins the launcher lists in OPENFLIGHT_ALLOWED_ORIGINS (space- or
// comma-separated): the kiosk UI and, on a unit whose screen shows the tester
// page, that page too, so one window switches between the two. Every other
// origin, and anything that is not http(s), is refused. Kept apart from
// main.js so it can be unit-tested without the `electron` module.

function webOrigin(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : null;
  } catch {
    return null;
  }
}

export function allowedOrigins(env, targetUrl) {
  const listed = String(env.OPENFLIGHT_ALLOWED_ORIGINS || '').split(/[\s,]+/);
  const origins = new Set();
  for (const entry of [targetUrl, ...listed]) {
    const origin = webOrigin(entry);
    if (origin) origins.add(origin);
  }
  return origins;
}

export function isNavigationAllowed(url, origins) {
  const origin = webOrigin(url);
  return origin !== null && origins.has(origin);
}
