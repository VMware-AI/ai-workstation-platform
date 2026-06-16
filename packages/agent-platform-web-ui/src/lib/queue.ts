import { Queue, Worker, Job } from "bullmq";
import IORedis from "ioredis";

const redisUrl = process.env.REDIS_URL || "redis://localhost:6379";
const parsed = new URL(redisUrl);

export const connection = new IORedis({
  host: parsed.hostname,
  port: Number(parsed.port) || 6379,
  password: parsed.password || undefined,
  maxRetriesPerRequest: null,
});

export const PROVISION_QUEUE = "provision";

export interface ProvisionJobData {
  instanceId: string;
  tenantId: string;
  computePoolId?: string;
}

export const provisionQueue = new Queue<ProvisionJobData>(PROVISION_QUEUE, {
  connection,
  defaultJobOptions: {
    attempts: 3,
    backoff: { type: "exponential", delay: 5000 },
    removeOnComplete: { count: 100 },
    removeOnFail: { count: 50 },
  },
});

// Enqueue with a hard timeout. When Redis is down, BullMQ's add() hangs forever
// inside waitUntilReady (the connection retries indefinitely), so the deploy API
// could never tell that enqueue failed. Race it against a timeout so the caller
// can mark the instance ERROR instead of leaving a silent PENDING orphan.
export class EnqueueTimeoutError extends Error {}

export async function enqueueProvision(
  data: ProvisionJobData,
  timeoutMs = 5000
): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new EnqueueTimeoutError("制备任务入队超时：队列服务（Redis）不可达。")),
      timeoutMs
    );
  });
  try {
    await Promise.race([provisionQueue.add("provision", data), timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Count workers currently connected to the provision queue (via Redis CLIENT
// LIST, matching BullMQ's worker client-name pattern). The deploy API uses this
// to warn when a job was enqueued but nothing is online to consume it (#259) —
// the exact gap behind repeated "stuck at PENDING" reports.
//
// Never throws and never blocks a deploy that already succeeded: a Redis hiccup
// here returns null (= "couldn't determine"), so the caller skips the warning
// rather than crying wolf. Raced against a short timeout for the same reason.
export async function countProvisionWorkers(timeoutMs = 2000): Promise<number | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
  });
  // Resolve the rejection to null at the source so the race itself never
  // rejects (and no stray rejection can surface elsewhere).
  const counted = provisionQueue
    .getWorkersCount()
    .catch(() => null as number | null);
  try {
    return await Promise.race([counted, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Worker concurrency (#257 M22). The old hardcoded 5 starved at 6 simultaneous
// provisions: each job holds a slot up to ~5 min inside `waitForIp`
// (`govc vm.ip -wait`). Configurable via PROVISION_CONCURRENCY so ops can tune
// to their vCenter, with the default raised to 10 so the common "6 concurrent"
// case no longer starves. The deeper fix — drop the synchronous IP wait and let
// the /api/nodes/register callback drive RUNNING — is tracked in #257 and needs
// the bootstrap/register path made mandatory + P2-e real-VM validation first.
export const DEFAULT_PROVISION_CONCURRENCY = 10;
// Each slot can hold a ~5 min synchronous `govc vm.ip -wait` against vCenter,
// so an over-large value floods vCenter with concurrent calls. Clamp a
// fat-fingered PROVISION_CONCURRENCY (e.g. 500) to a sane ceiling rather than
// accepting it silently. Raise this only alongside the #257 async-register fix.
export const MAX_PROVISION_CONCURRENCY = 50;

export function resolveProvisionConcurrency(
  env: Record<string, string | undefined> = process.env,
): number {
  const raw = Number(env.PROVISION_CONCURRENCY);
  if (!Number.isInteger(raw) || raw <= 0) return DEFAULT_PROVISION_CONCURRENCY;
  return Math.min(raw, MAX_PROVISION_CONCURRENCY);
}

export function createWorker(
  processor: (job: Job<ProvisionJobData>) => Promise<void>
): Worker<ProvisionJobData> {
  return new Worker<ProvisionJobData>(PROVISION_QUEUE, processor, {
    connection,
    concurrency: resolveProvisionConcurrency(),
  });
}
