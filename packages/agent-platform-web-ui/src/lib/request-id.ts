// Correlation/request-ID resolution (harness H-12, #213).
// Same conservative charset/length rule as control's request_id.py —
// an attacker-controlled header that lands verbatim in log lines is a
// log-injection vector, so anything unsafe is replaced with a fresh ID.

export const REQUEST_ID_HEADER = "X-Request-ID";

const SANE_ID = /^[A-Za-z0-9._-]{1,128}$/;

export function normalizeRequestId(value: string | null): string | null {
  if (value && SANE_ID.test(value)) return value;
  return null;
}

export function resolveRequestId(incoming: string | null): string {
  return normalizeRequestId(incoming) ?? crypto.randomUUID();
}
