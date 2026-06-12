// Bundle the provision worker with a build fingerprint baked in (#313).
// Git sha resolution order: $GIT_SHA (Docker build arg) → `git rev-parse`
// (local build) → "unknown" — a missing git must not break the build.
import { execFileSync } from "node:child_process";

function resolveSha() {
  const fromEnv = (process.env.GIT_SHA ?? "").trim();
  if (fromEnv) return fromEnv;
  try {
    return execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim();
  } catch {
    return "unknown";
  }
}

const sha = resolveSha().replace(/[^0-9A-Za-z._-]/g, "").slice(0, 64) || "unknown";
const builtAt = new Date().toISOString();
execFileSync(
  "npx",
  [
    "esbuild",
    "worker/index.ts",
    "--bundle",
    "--platform=node",
    "--target=node20",
    "--packages=external",
    "--tsconfig=tsconfig.worker.json",
    "--outfile=dist/worker/index.js",
    `--define:__BUILD_GIT_SHA__="${JSON.stringify(sha).slice(1, -1)}"`,
    `--define:__BUILD_TIME__="${JSON.stringify(builtAt).slice(1, -1)}"`,
  ],
  { stdio: "inherit" }
);
console.log(`[build-worker] git=${sha} built=${builtAt}`);
