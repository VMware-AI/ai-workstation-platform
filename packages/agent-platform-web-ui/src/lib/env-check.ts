// Startup fail-fast for required secrets (harness H-13, issue #214).
// Mirrors the control plane's production_safety_problems(): collect every
// problem, then refuse to boot with one teaching error — instead of a
// first-request 500 (the root cause behind issue #203).

type EnvLike = Record<string, string | undefined>;

export function requiredEnvProblems(env: EnvLike): string[] {
  const problems: string[] = [];

  const keyHex = env.ENCRYPTION_KEY;
  if (!keyHex) {
    problems.push(
      "ENCRYPTION_KEY is missing (64 hex chars = 32 bytes; encrypts stored vCenter passwords / API keys)"
    );
  } else {
    // Mirror crypto.ts loadKey(): must decode to exactly 32 bytes.
    const key = Buffer.from(keyHex, "hex");
    if (key.length !== 32) {
      problems.push(
        `ENCRYPTION_KEY must decode to exactly 32 bytes; got ${key.length}`
      );
    }
  }

  // INTERNAL_API_SECRET is deliberately NOT required: it became orphaned
  // config when the docker-agent runtime was removed (#180 — agent auth is
  // per-instance agentTokenHash now). It has since been dropped from
  // setup-env.sh / .env.example (#357); nothing reads it, so enforcing it at
  // startup would refuse boot over a dead variable (review finding on PR #218).

  return problems;
}

export function assertRequiredEnv(env: EnvLike = process.env): void {
  const problems = requiredEnvProblems(env);
  if (problems.length > 0) {
    throw new Error(
      "Refusing to start with missing/invalid required env vars:\n" +
        problems.map((p) => `  - ${p}`).join("\n") +
        "\nFix: run `npm run setup:env` (generates missing secrets into .env), " +
        "or set them in the environment. See SETUP.md."
    );
  }
}

// Exit-on-failure variant for server boot paths (instrumentation.ts). Throwing
// there is not enough: `next start` logs the instrumentation error as an
// unhandledRejection and keeps serving 500s (verified on Next 16.2.4) —
// "refuses to start" must mean the process actually exits. It lives HERE, in a
// dynamically imported module, so the edge bundle's static analysis never sees
// a Node-only API — instrumentation.ts referencing process.exit directly made
// Next warn on every request and the noise derailed real debugging (#260).
export function assertRequiredEnvOrExit(env: EnvLike = process.env): void {
  try {
    assertRequiredEnv(env);
  } catch (e) {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  }
}
