import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { withTenantRole } from "@/lib/tenant";
import { VSphereClient } from "@/lib/providers/vsphere/client";
import { teachVcError } from "@/lib/providers/vsphere/teach-vc-error";
import { logger } from "@/lib/logger";

// Validate a vSphere connection before the pool is saved. Credentials arrive in
// the request body (the pool doesn't exist yet) — protected by same-origin CSRF
// + session auth + OWNER/ADMIN role. They are used transiently and never logged.
const schema = z.object({
  host: z.string().min(1),
  username: z.string().min(1),
  password: z.string().min(1),
  verifySsl: z.boolean().optional(),
});

export async function POST(req: NextRequest) {
  return withTenantRole(req, async () => {
    const body = await req.json().catch(() => null);
    const parsed = schema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { ok: false, error: "参数无效：需要 host / username / password。" },
        { status: 400 }
      );
    }
    const { host, username, password, verifySsl } = parsed.data;
    const client = new VSphereClient({
      host,
      username,
      password,
      datacenter: "",
      templateName: "",
      verifySsl,
    });
    try {
      await client.testConnection();
      return NextResponse.json({ ok: true });
    } catch (e) {
      // The raw VSphereClient error embeds the upstream HTTP status + body;
      // map it to a teaching message so that never reaches the browser. Full
      // detail (incl. the body) goes to the server log only.
      const raw = e instanceof Error ? e.message : String(e);
      logger.error("compute-pool test connection failed", { scope: "compute-pools", error: raw });
      return NextResponse.json({
        ok: false,
        error: teachVcError(raw, {
          certHint: "TLS 证书校验失败：自签名证书环境请勾选「跳过证书校验」(verifySsl=false)。",
        }),
      });
    }
  });
}
