// Session lifetime constants (#90 M1), extracted from auth.ts so the worker
// can share them (#239 session sweep) — auth.ts pulls in react/next-headers,
// which must not leak into the worker bundle.
//
// 7-day absolute cap + 24h sliding idle window: a stolen cookie is only
// useful while the victim's session stays warm. lastSeenAt is bumped at most
// once an hour to keep per-request write amplification negligible.
export const SESSION_ABSOLUTE_MS = 7 * 24 * 60 * 60 * 1000;
export const SESSION_IDLE_MS = 24 * 60 * 60 * 1000;
export const SESSION_TOUCH_INTERVAL_MS = 60 * 60 * 1000;
