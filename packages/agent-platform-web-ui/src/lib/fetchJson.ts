// Small client-side fetch wrapper that collapses the repeated
// `fetch → res.ok → res.json → catch` boilerplate scattered across the
// dashboard pages (#357 item 2/3). It always returns a discriminated result
// so callers handle success and failure explicitly instead of letting a
// rejected promise strand a button or push a `{error}` object into list state.
//
// On a non-2xx response it tries to read `{ error }` from the body (the API's
// standard error envelope) and falls back to a status-code message. On a
// network/parse failure it returns a generic message — never throws.

export type FetchJsonResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number };

const NETWORK_ERROR = "网络错误，请稍后重试";

function extractError(body: unknown, status: number): string {
  if (body && typeof body === "object" && "error" in body) {
    const err = (body as { error: unknown }).error;
    if (typeof err === "string" && err.length > 0) return err;
    // zod flatten() shape: { error: { formErrors: string[] } }
    if (err && typeof err === "object" && "formErrors" in err) {
      const fe = (err as { formErrors?: unknown }).formErrors;
      if (Array.isArray(fe) && typeof fe[0] === "string") return fe[0];
    }
  }
  return `请求失败（HTTP ${status}）`;
}

export async function fetchJson<T = unknown>(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<FetchJsonResult<T>> {
  let res: Response;
  try {
    res = await fetch(input, init);
  } catch {
    // Network failure (offline, DNS, aborted connection) — never surfaces as
    // an unhandled rejection that leaves a button disabled forever.
    return { ok: false, error: NETWORK_ERROR, status: 0 };
  }

  // A 500 can return an empty/non-JSON body; never let res.json() throw.
  const body = await res.json().catch(() => null);

  if (!res.ok) {
    return { ok: false, error: extractError(body, res.status), status: res.status };
  }
  return { ok: true, data: (body ?? null) as T };
}
