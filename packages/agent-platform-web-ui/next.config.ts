import type { NextConfig } from "next";

import { buildSecurityHeaders } from "./src/lib/security-headers";

// Accessing the dev server from another machine on the LAN (e.g.
// http://<your-ip>:3000) is blocked by Next unless that origin is allowlisted.
// Set DEV_ALLOWED_ORIGINS in .env to your access host(s), comma-separated —
// e.g. DEV_ALLOWED_ORIGINS=172.20.20.6,192.168.1.50  (host only, no scheme).
// Dev-server only; has no effect on production builds.
const devAllowedOrigins = (process.env.DEV_ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  serverExternalPackages: ["bcryptjs"],
  ...(devAllowedOrigins.length > 0 ? { allowedDevOrigins: devAllowedOrigins } : {}),
  // Baseline security headers on every route (#230). CSP is report-only for
  // now — see src/lib/security-headers.ts for the enforce plan.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: buildSecurityHeaders(
          process.env.NODE_ENV === "development" ? "development" : "production"
        ),
      },
    ];
  },
};

export default nextConfig;
