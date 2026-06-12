import { NextRequest, NextResponse } from "next/server";
import { REQUEST_ID_HEADER, resolveRequestId } from "@/lib/request-id";

// CSRF defense: state-changing requests must originate from the same origin
// as the app. Cookie auth with SameSite=lax is not sufficient on its own —
// a cross-site form-post can still mutate state for a logged-in admin.
//
// Routes that authenticate via Authorization: Bearer (not cookies) are
// exempt: they cannot be triggered by a cross-site form-post in a browser
// because browsers do not attach arbitrary Authorization headers from
// cross-origin form-posts.

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const BEARER_AUTHED_PATHS = new Set([
  "/api/nodes/register", // agent self-registration, Bearer token
  "/api/nodes/credentials", // one-shot agent credential fetch, Bearer token
]);

export function middleware(req: NextRequest) {
  // Correlation ID (harness H-12, #213): keep the caller's X-Request-ID when
  // sane, generate one otherwise. Forward it to route handlers (request
  // headers) and echo it on every response so one ID greps across
  // web-ui → gateway → control logs.
  const requestId = resolveRequestId(req.headers.get(REQUEST_ID_HEADER));
  const forwardedHeaders = new Headers(req.headers);
  forwardedHeaders.set(REQUEST_ID_HEADER, requestId);
  const next = () => {
    const res = NextResponse.next({ request: { headers: forwardedHeaders } });
    res.headers.set(REQUEST_ID_HEADER, requestId);
    return res;
  };

  if (SAFE_METHODS.has(req.method)) {
    return next();
  }
  if (BEARER_AUTHED_PATHS.has(req.nextUrl.pathname)) {
    return next();
  }

  // Compare the Origin header's host against the Host header — i.e. what the
  // client actually connected to. This is the reliable same-origin signal:
  // it works identically for localhost, 127.0.0.1, a LAN IP, or behind a
  // reverse proxy. (req.nextUrl.origin is NOT reliable under `next dev` — it
  // can reflect the server's bound host/port rather than the request's, so
  // legitimate local POSTs from 127.0.0.1 vs localhost were rejected.)
  //
  // A cross-site form-post from an attacker page still carries that page's
  // Origin, whose host won't match our Host header — so CSRF is still blocked.
  const origin = req.headers.get("origin");
  const host = req.headers.get("host");

  let originHost: string | null = null;
  if (origin) {
    try {
      originHost = new URL(origin).host;
    } catch {
      originHost = null;
    }
  }

  if (!originHost || !host || originHost !== host) {
    return NextResponse.json(
      { error: "Cross-origin request rejected" },
      { status: 403, headers: { [REQUEST_ID_HEADER]: requestId } }
    );
  }
  return next();
}

export const config = {
  matcher: ["/api/:path*"],
};
