import { prisma } from "@/lib/prisma";
import { SESSION_IDLE_MS } from "@/lib/session-config";
import { logger } from "@/lib/logger";

// Session-row sweep (#239 NIT): expired/idle sessions are deleted lazily on
// access, so abandoned ones lingered forever. This bounds the table — the
// live checks in auth.ts stay authoritative; the sweep is pure hygiene.
export async function runSessionSweep(): Promise<void> {
  const now = Date.now();
  try {
    const res = await prisma.session.deleteMany({
      where: {
        OR: [
          { expiresAt: { lt: new Date(now) } },
          { lastSeenAt: { lt: new Date(now - SESSION_IDLE_MS) } },
        ],
      },
    });
    if (res.count > 0) {
      logger.info("session sweep: removed stale sessions", { count: res.count });
    }
  } catch (e) {
    // Hygiene must never crash the scheduler; the next tick retries.
    logger.error("session sweep failed", { error: e });
  }
}
