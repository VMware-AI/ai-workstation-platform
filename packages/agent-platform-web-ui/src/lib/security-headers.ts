// Security response headers (#230). Wired into next.config.ts `headers()` so
// every route — pages, API, static assets — carries the baseline set.
//
// CSP ships as Content-Security-Policy-Report-Only first: violations show up
// in the browser console (and reporting endpoints, once configured) without
// breaking pages. Flip to enforcing Content-Security-Policy only after a
// soak period shows no violations on real workflows (#230 acceptance).
//
// Why no nonce: a nonce-based CSP requires per-request header generation in
// proxy/middleware plus forced dynamic rendering of every page. Report-only
// with 'unsafe-inline' is the agreed first step; tighten when enforcing.

type Mode = "development" | "production" | "test";

interface Header {
  readonly key: string;
  readonly value: string;
}

function buildCsp(mode: Mode): string {
  const dev = mode === "development";
  const directives = [
    "default-src 'self'",
    // Next.js hydration uses inline scripts; dev needs eval for React's
    // enhanced debugging (not used in production builds).
    `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
    // Tailwind/Next inject inline style tags.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    // Dev HMR connects over a websocket.
    // TODO(enforce): tighten to ws://localhost:* — scheme-wide ws: is fine
    // for report-only dev but must not become the enforce template.
    `connect-src 'self'${dev ? " ws:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

export function buildSecurityHeaders(mode: Mode): Header[] {
  return [
    // Ignored by browsers over plain HTTP; effective as soon as the app is
    // served behind TLS. No `preload` — that is a hard-to-reverse commitment.
    {
      key: "Strict-Transport-Security",
      value: "max-age=31536000; includeSubDomains",
    },
    // Legacy twin of CSP frame-ancestors 'none', for older user agents.
    { key: "X-Frame-Options", value: "DENY" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    { key: "Content-Security-Policy-Report-Only", value: buildCsp(mode) },
  ];
}
