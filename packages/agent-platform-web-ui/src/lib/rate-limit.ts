// In-memory sliding-window rate limiter (#90 M5 — login/register had none;
// bcrypt cost 12 slows a single attempt but nothing gated volume).
//
// Scope notes:
// - State is per-process. The web app runs as ONE Next.js server (the BullMQ
//   worker is a separate process but serves no HTTP). A multi-replica
//   deployment would move this to Redis — keep the call sites, swap the store.
// - clientIp() reads x-forwarded-for, which is CLIENT-CONTROLLED until a
//   trusted reverse proxy sets it (PR #237 review HIGH-1): a deliberate
//   attacker can rotate fake IPs past any ip-keyed limit. IP keys therefore
//   only stop naive tooling and accidental floods; account-targeted brute
//   force is bounded by the SECONDARY per-email cap on login (see
//   login/route.ts). Switch to rightmost-hop parsing once a trusted proxy
//   fronts the app.

type Bucket = number[]; // timestamps (ms) of counted attempts, oldest first

const buckets = new Map<string, Bucket>();

// Eviction (PR #237 review MEDIUM-1): keys are attacker-influencable
// (ip+email), so the Map must not grow without bound. When it exceeds
// MAX_KEYS, drop every bucket whose newest attempt is older than the
// largest window any caller uses.
const MAX_KEYS = 10_000;
const MAX_WINDOW_MS = 60 * 60 * 1000;

function evictStale(now: number): void {
  if (buckets.size <= MAX_KEYS) return;
  for (const [key, bucket] of buckets) {
    if (bucket.length === 0 || bucket[bucket.length - 1] < now - MAX_WINDOW_MS) {
      buckets.delete(key);
    }
  }
}

export interface RateLimitOptions {
  limit: number;
  windowMs: number;
}

export interface RateLimitResult {
  allowed: boolean;
  retryAfterS: number;
}

export function rateLimit(key: string, { limit, windowMs }: RateLimitOptions): RateLimitResult {
  const now = Date.now();
  evictStale(now);
  const cutoff = now - windowMs;
  const bucket = (buckets.get(key) ?? []).filter((t) => t > cutoff);

  if (bucket.length >= limit) {
    // Do NOT record blocked attempts — counting them would let an attacker
    // (or an angry user) extend the lockout forever.
    buckets.set(key, bucket);
    return { allowed: false, retryAfterS: Math.ceil((bucket[0] + windowMs - now) / 1000) };
  }

  bucket.push(now);
  buckets.set(key, bucket);
  return { allowed: true, retryAfterS: 0 };
}

/** First hop of x-forwarded-for, or a stable sentinel. Behind the expected
 * single reverse proxy this is the client; without one it's still a useful
 * per-source key. */
export function clientIp(headers: Headers): string {
  const xff = headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  return "unknown";
}

/** Test hook. */
export function clearRateLimits(): void {
  buckets.clear();
}
