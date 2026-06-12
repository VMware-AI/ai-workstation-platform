import { encrypt, decrypt } from "./crypto";

// Helpers for ComputePool.config to keep the vSphere password encrypted
// at rest and out of any GET response body.
//
// Storage shape: { ..., passwordEncrypted: "<iv:tag:ciphertext>" }
// Wire-out shape: { ..., password: "****" } — sentinel so the UI knows a
// password exists, without exposing the encrypted blob to the browser.
// Consumer shape: decoded plaintext via decryptPoolPassword().

export const PASSWORD_SENTINEL = "****";

type AnyConfig = Record<string, unknown>;

// Returns true if a string looks like the iv:tag:ciphertext format
// emitted by lib/crypto.encrypt(). Used so consumers can transparently
// handle legacy plaintext rows that pre-date the at-rest encryption.
function looksEncrypted(s: string): boolean {
  const parts = s.split(":");
  if (parts.length !== 3) return false;
  return parts.every((p) => /^[0-9a-f]+$/i.test(p) && p.length > 0);
}

// Take a config object as supplied by an API caller (containing
// `password: "<plaintext>"` or the sentinel) and return the persisted
// shape (containing `passwordEncrypted` only). `previousEncrypted` lets
// PATCH preserve the existing encrypted value when the caller submits
// the sentinel.
export function encryptPoolConfigForStorage(
  incoming: AnyConfig,
  previousEncrypted?: string
): AnyConfig {
  const out: AnyConfig = { ...incoming };
  const raw = out.password;
  delete out.password;
  delete out.passwordEncrypted;

  if (typeof raw === "string" && raw.length > 0 && raw !== PASSWORD_SENTINEL) {
    out.passwordEncrypted = encrypt(raw);
  } else if (previousEncrypted) {
    out.passwordEncrypted = previousEncrypted;
  }
  return out;
}

// Take a stored config and return a redacted shape safe to return
// from a GET endpoint. The encrypted blob is never on the wire.
export function redactPoolConfigForWire(stored: AnyConfig): AnyConfig {
  const out: AnyConfig = { ...stored };
  const hasSecret =
    typeof out.passwordEncrypted === "string" && (out.passwordEncrypted as string).length > 0;
  // Also catch legacy rows that still carry a plaintext `password`.
  const hasLegacyPlaintext = typeof out.password === "string" && (out.password as string).length > 0;
  delete out.passwordEncrypted;
  delete out.password;
  if (hasSecret || hasLegacyPlaintext) {
    out.password = PASSWORD_SENTINEL;
  }
  return out;
}

// Resolve the plaintext password for a consumer (the VSphereProvider).
// Falls back to a legacy plaintext field for any row that pre-dates
// the encryption migration. Returns "" if no password is set at all.
export function decryptPoolPassword(stored: AnyConfig): string {
  const enc = stored.passwordEncrypted;
  if (typeof enc === "string" && enc.length > 0) {
    if (looksEncrypted(enc)) {
      return decrypt(enc);
    }
    // Defensive: a malformed value should not crash the worker.
    return "";
  }
  const legacy = stored.password;
  return typeof legacy === "string" ? legacy : "";
}
