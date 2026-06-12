import { NextRequest, NextResponse } from "next/server";
import http from "node:http";
import https from "node:https";
import { z } from "zod";
import { withTenant, withTenantRole } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { encrypt, decrypt, maskApiKey } from "@/lib/crypto";
import { assertSafeHttpUrl, resolveSafeHttpUrl, UnsafeUrlError } from "@/lib/url-safe";

const patchSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  endpoint: z.string().optional(),
  apiKey: z.string().optional(),
  defaultModel: z.string().optional(),
  enabled: z.boolean().optional(),
});

function fetchStatusWithPinnedLookup(
  url: URL,
  pinned: { address: string; family: number },
  headers: Record<string, string>
): Promise<number> {
  return new Promise((resolve, reject) => {
    const options: http.RequestOptions = {
      method: "GET",
      headers,
      timeout: 8000,
      lookup: (_hostname, _options, cb) => cb(null, pinned.address, pinned.family),
    };
    const onResponse = (res: http.IncomingMessage) => {
      const status = res.statusCode ?? 0;
      res.resume();
      res.on("end", () => resolve(status));
    };
    const req =
      url.protocol === "http:"
        ? http.request(url, options, onResponse)
        : https.request(url, options, onResponse);
    req.on("timeout", () => {
      const err = new Error("Request timed out");
      err.name = "TimeoutError";
      req.destroy(err);
    });
    req.on("error", reject);
    req.end();
  });
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenantRole(req, async (session) => {
    const { id } = await params;
    const provider = await prisma.modelProvider.findFirst({
      where: { id, tenantId: session.tenantId },
    });
    if (!provider) return NextResponse.json({ error: "Not found" }, { status: 404 });

    // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
    const body = await req.json().catch(() => null);
    const parsed = patchSchema.safeParse(body);
    if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

    const { apiKey, endpoint, ...rest } = parsed.data;
    if (endpoint) {
      try {
        await assertSafeHttpUrl(endpoint);
      } catch (e) {
        const msg = e instanceof UnsafeUrlError ? e.message : "Invalid endpoint URL.";
        return NextResponse.json({ error: msg }, { status: 400 });
      }
    }
    const updated = await prisma.modelProvider.update({
      // Atomic scope (#90 M6): ownership was pre-checked, the write carries
      // the filter too.
      where: { id, tenantId: session.tenantId },
      data: {
        ...rest,
        ...(endpoint !== undefined ? { endpoint: endpoint || null } : {}),
        ...(apiKey ? { apiKeyEncrypted: encrypt(apiKey) } : {}),
      },
    });
    return NextResponse.json({ ...updated, apiKeyEncrypted: maskApiKey(updated.apiKeyEncrypted) });
  });
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return withTenantRole(req, async (session) => {
    const { id } = await params;
    const provider = await prisma.modelProvider.findFirst({
      where: { id, tenantId: session.tenantId },
    });
    if (!provider) return NextResponse.json({ error: "Not found" }, { status: 404 });
    await prisma.modelProvider.delete({ where: { id, tenantId: session.tenantId } });
    return NextResponse.json({ ok: true });
  });
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  // Test connectivity — uses the stored credential, so gate to managers too.
  return withTenantRole(req, async (session) => {
    const { id } = await params;
    const provider = await prisma.modelProvider.findFirst({
      where: { id, tenantId: session.tenantId },
    });
    if (!provider) return NextResponse.json({ error: "Not found" }, { status: 404 });

    const apiKey = decrypt(provider.apiKeyEncrypted);
    try {
      let testUrl: string;
      let headers: Record<string, string>;
      if (provider.type === "openai" || provider.type === "azure") {
        const baseUrl = provider.endpoint || "https://api.openai.com/v1";
        testUrl = `${baseUrl}/models`;
        headers = { Authorization: `Bearer ${apiKey}` };
      } else if (provider.type === "anthropic") {
        testUrl = "https://api.anthropic.com/v1/models";
        headers = { "x-api-key": apiKey, "anthropic-version": "2023-06-01" };
      } else {
        return NextResponse.json({ ok: false, message: "连接失败: 不支持的 provider 类型" }, { status: 400 });
      }

      // Re-validate at fetch time to narrow the DNS-rebinding window between
      // write-time validation and the actual outbound request.
      let safeUrl: URL;
      let pinnedAddress: { address: string; family: number };
      try {
        const resolved = await resolveSafeHttpUrl(testUrl);
        const firstAddress = resolved.addresses[0];
        if (!firstAddress) throw new UnsafeUrlError("Endpoint hostname did not resolve.");
        safeUrl = resolved.url;
        pinnedAddress = firstAddress;
      } catch (e) {
        const msg = e instanceof UnsafeUrlError ? e.message : "Endpoint blocked.";
        return NextResponse.json({ ok: false, message: `连接失败: ${msg}` }, { status: 400 });
      }

      const status = await fetchStatusWithPinnedLookup(safeUrl, pinnedAddress, headers);
      if (status < 200 || status >= 300) {
        // Surface only the status code — never echo the upstream body, which
        // could contain prompt-injection bait or internal infra details.
        return NextResponse.json(
          { ok: false, message: `连接失败: 上游 HTTP ${status}` },
          { status: 400 }
        );
      }
      return NextResponse.json({ ok: true, message: "连接测试成功" });
    } catch (e: unknown) {
      // Map errors to a generic class so error text never includes
      // attacker-controlled response data.
      const name = e instanceof Error ? e.name : "Error";
      const reason =
        name === "TimeoutError" || name === "AbortError"
          ? "请求超时"
          : name === "TypeError"
          ? "无法建立连接"
          : "请求失败";
      return NextResponse.json({ ok: false, message: `连接失败: ${reason}` }, { status: 400 });
    }
  });
}
