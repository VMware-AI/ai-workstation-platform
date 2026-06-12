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

export function createWorker(
  processor: (job: Job<ProvisionJobData>) => Promise<void>
): Worker<ProvisionJobData> {
  return new Worker<ProvisionJobData>(PROVISION_QUEUE, processor, {
    connection,
    concurrency: 5,
  });
}
