import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { prisma } from "@/lib/prisma";
import { transitionInstance } from "@/lib/instance-transition";

const schema = z.object({
  instanceId: z.string(),
  status: z.literal("READY"),
  ipAddress: z.string().optional(),
  endpoint: z.string().optional(),
});

export async function POST(req: NextRequest) {
  const token = req.headers.get("authorization")?.replace("Bearer ", "");
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const body = await req.json();
  const parsed = schema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ error: "Invalid body" }, { status: 400 });

  const { instanceId, ipAddress, endpoint } = parsed.data;

  // tenant-scope: bearer-token endpoint — the one-shot agent token
  // (sha256-checked below) is the authority; there is no session.
  const instance = await prisma.instance.findUnique({ where: { id: instanceId } });
  if (!instance) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Token hash MUST be present — refuse registration otherwise. Without this
  // check, any null-hash row (the current schema default) lets an attacker
  // POST any instanceId + endpoint and flip it to RUNNING. Provision flow is
  // responsible for writing agentTokenHash; see issue #86.
  if (!instance.agentTokenHash) {
    return NextResponse.json(
      {
        error:
          "Instance has no registration token on file; provision did not " +
          "complete or token already consumed",
      },
      { status: 409 }
    );
  }

  const crypto = await import("crypto");
  const hash = crypto.createHash("sha256").update(token).digest("hex");
  // Timing-safe compare (PR #253 review L-3), same pattern as
  // /api/nodes/credentials.
  const expected = Buffer.from(instance.agentTokenHash, "hex");
  const got = Buffer.from(hash, "hex");
  if (expected.length !== got.length || !crypto.timingSafeEqual(expected, got)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  // A hash with no expiry would be a never-dying token (PR #253 review
  // L-4) — cannot happen via the worker (both written atomically), but
  // fail closed against any other writer.
  if (!instance.agentTokenExpiresAt || instance.agentTokenExpiresAt < new Date()) {
    return NextResponse.json({ error: "Token expired" }, { status: 401 });
  }

  // Guarded (#244): self-registration confirms the VM is up — valid only
  // from a provisioning-ish state. A row a stop/delete already owns must
  // not be flipped back to RUNNING by a late-arriving agent call.
  // RUNNING is deliberately in the from-set: the worker's vm.ip-wait path
  // may have marked the row RUNNING before the VM's callback arrives — the
  // callback must still succeed (and consume its token). A SECOND callback
  // is rejected earlier by the null-hash check (one-shot token), so this
  // is not re-registration.
  const registered = await transitionInstance(
    instanceId,
    ["PENDING", "PROVISIONING", "INITIALIZING", "RUNNING"],
    "RUNNING",
    {
      startedAt: new Date(),
      ...(ipAddress ? { ipAddress } : {}),
      ...(endpoint ? { endpoint } : {}),
      agentTokenHash: null,
    }
  );
  if (!registered) {
    return NextResponse.json(
      { error: "Instance is not in a registrable state (stopped or deleted)" },
      { status: 409 }
    );
  }

  await prisma.provisionEvent.create({
    data: { instanceId, event: "AGENT_REGISTERED", message: "Agent self-registered as ready" },
  });

  return NextResponse.json({ ok: true });
}
