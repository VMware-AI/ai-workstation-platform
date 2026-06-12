import { NextRequest, NextResponse } from "next/server";
import crypto from "crypto";
import { prisma } from "@/lib/prisma";

// One-shot credentials fetch for a freshly-provisioned agent VM.
//
// The agent receives its bearer token through cloud-init, calls this
// endpoint over the control-plane URL, and writes the plaintext API key
// into a tmpfs file consumed by `docker run --env-file`. The API key
// therefore never lands in cloud-init user-data or on the VM disk.
//
// Auth: same Bearer + sha256 check as POST /api/nodes/register. The
// token is NOT cleared here (register clears it after the agent reports
// READY), so this endpoint can only be called while bootstrapping a
// brand-new instance — once register fires, the token hash is wiped
// and further credential fetches return 401.

export async function POST(req: NextRequest) {
  const token = req.headers.get("authorization")?.replace("Bearer ", "");
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await req.json().catch(() => ({}))) as { instanceId?: string };
  const instanceId = body.instanceId;
  if (!instanceId || typeof instanceId !== "string") {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  // tenant-scope: bearer-token endpoint — the one-shot agent token
  // (sha256-checked below) is the authority; there is no session.
  const instance = await prisma.instance.findUnique({
    where: { id: instanceId },
  });
  if (!instance) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  if (!instance.agentTokenHash) {
    // Token was already consumed by /nodes/register, or instance was
    // never issued a bootstrap token. Either way, credentials fetch is
    // no longer permitted.
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const candidateHash = crypto.createHash("sha256").update(token).digest("hex");
  // Use timing-safe equality so callers cannot brute-force the token
  // through response-time analysis.
  const expected = Buffer.from(instance.agentTokenHash, "hex");
  const got = Buffer.from(candidateHash, "hex");
  if (expected.length !== got.length || !crypto.timingSafeEqual(expected, got)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  // Fail closed, same as /nodes/register (PR #253 L-4): a hash with NO
  // expiry must be rejected — never-dying tokens are not allowed.
  if (!instance.agentTokenExpiresAt || instance.agentTokenExpiresAt < new Date()) {
    return NextResponse.json({ error: "Token expired" }, { status: 401 });
  }

  // Token is valid, but LLM credential delivery from deployParams (llmBaseUrl /
  // llmApiKey, doc 33 §5.5) is not yet wired into cloud-init (doc 33 §9 TODO).
  // The legacy Template/modelProvider key source was removed (P2-c.3).
  return NextResponse.json(
    { error: "LLM credential delivery not yet implemented (doc 33 §9)" },
    { status: 501 },
  );
}
