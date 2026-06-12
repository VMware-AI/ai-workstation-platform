// Runs once when the Next.js server starts, before it serves requests —
// the fail-fast hook for required secrets (harness H-13, issue #214).
//
// Keep this file free of Node-only APIs (process.exit etc.): Next statically
// analyzes it for the edge bundle and warns on EVERY request otherwise (#260).
// The exit-on-failure logic lives in env-check.ts behind the dynamic import.
export async function register(): Promise<void> {
  // Only the Node.js server runtime needs (and can read) the secrets;
  // skip the edge runtime and the production build phase, where no
  // requests are served and .env may legitimately be absent (CI builds).
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (process.env.NEXT_PHASE === "phase-production-build") return;

  const { assertRequiredEnvOrExit } = await import("@/lib/env-check");
  assertRequiredEnvOrExit();
}
