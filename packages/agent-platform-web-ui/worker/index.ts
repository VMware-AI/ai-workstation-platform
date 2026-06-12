import "dotenv/config";
import { assertRequiredEnv } from "@/lib/env-check";
import { CLONE_TIMEOUT_MS } from "@/lib/providers/vsphere/govc";
import { createWorker } from "@/lib/queue";
import { processProvisionJob } from "./jobs/provision";
import { runReaper } from "./jobs/reaper";
import { runSessionSweep } from "./jobs/session-sweep";

// Fail fast at startup: the worker decrypts stored vCenter credentials, so a
// missing ENCRYPTION_KEY must refuse to boot, not fail mid-provision (harness
// H-13, issue #214). Note: import hoisting means @/lib/queue's module body
// (which constructs the Redis client) runs before this line — the throw still
// exits the process in the same tick, before any job is consumed.
// 构建指纹（#313）先于一切打印：旧容器/旧 bundle 误诊一行可断——esbuild
// --define 注入，tsx 直跑源码时为 dev/now；缺 env 拒启时也能看到跑的是哪版。
// clone budget 直接读运行时常量，杜绝日志与实际不符。
declare const __BUILD_GIT_SHA__: string;
declare const __BUILD_TIME__: string;
const buildSha = typeof __BUILD_GIT_SHA__ !== "undefined" ? __BUILD_GIT_SHA__ : "dev(tsx)";
const buildTime = typeof __BUILD_TIME__ !== "undefined" ? __BUILD_TIME__ : "now";
console.log(
  `[worker] clone budget=${CLONE_TIMEOUT_MS / 60000}min git=${buildSha} built=${buildTime}`
);

assertRequiredEnv();

const worker = createWorker(processProvisionJob);

worker.on("completed", (job) => {
  console.log(`[worker] job ${job.id} completed`);
});

worker.on("failed", (job, err) => {
  console.error(`[worker] job ${job?.id} failed: ${err.message}`);
});

worker.on("error", (err) => {
  console.error("[worker] error:", err);
});

// Reaper: sweep stale provisioning instances every 5 minutes
const REAPER_INTERVAL = 5 * 60 * 1000;
runReaper().catch(console.error);
setInterval(() => runReaper().catch(console.error), REAPER_INTERVAL);

// Session sweep (#239): expired/idle session rows are otherwise only
// deleted lazily on access; abandoned ones lingered forever. Hourly is
// plenty — the live checks in auth.ts stay authoritative.
const SESSION_SWEEP_INTERVAL = 60 * 60 * 1000;
runSessionSweep().catch(console.error);
setInterval(() => runSessionSweep().catch(console.error), SESSION_SWEEP_INTERVAL);

console.log("[worker] provisioner started, waiting for jobs...");
console.log(`[worker] reaper running every ${REAPER_INTERVAL / 60000} min`);
console.log(`[worker] session sweep running every ${SESSION_SWEEP_INTERVAL / 60000} min`);

process.on("SIGTERM", async () => {
  await worker.close();
  process.exit(0);
});
