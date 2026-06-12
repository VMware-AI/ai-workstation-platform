import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { withTenant, withTenantRole } from "@/lib/tenant";
import { prisma } from "@/lib/prisma";
import { parsePageParams } from "@/lib/pagination";
import { encrypt, maskApiKey } from "@/lib/crypto";
import { assertSafeHttpUrl, UnsafeUrlError } from "@/lib/url-safe";

const schema = z.object({
  name: z.string().min(1).max(100),
  type: z.enum(["openai", "anthropic", "azure", "custom"]),
  endpoint: z.string().url().optional().or(z.literal("")),
  apiKey: z.string().min(1),
  defaultModel: z.string().min(1),
});

export async function GET(req: NextRequest) {
  return withTenant(req, async (session) => {
    const page = parsePageParams(new URL(req.url));
    if ("error" in page) return NextResponse.json({ error: page.error }, { status: 400 });
    const [providers, total] = await prisma.$transaction([
      prisma.modelProvider.findMany({
        where: { tenantId: session.tenantId },
        orderBy: { createdAt: "desc" },
        skip: page.skip,
        take: page.take, // #255: 真分页
      }),
      prisma.modelProvider.count({ where: { tenantId: session.tenantId } }),
    ]);
    return NextResponse.json({
      items: providers.map((p: typeof providers[number]) => ({
        ...p,
        apiKeyEncrypted: maskApiKey(p.apiKeyEncrypted),
      })),
      total,
      skip: page.skip,
      take: page.take,
    });
  });
}

export async function POST(req: NextRequest) {
  return withTenantRole(req, async (session) => {
    // Malformed/empty body must be a 400, not a 500 from req.json() throwing.
    const body = await req.json().catch(() => null);
    const parsed = schema.safeParse(body);
    if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

    const { name, type, endpoint, apiKey, defaultModel } = parsed.data;
    if (endpoint) {
      try {
        await assertSafeHttpUrl(endpoint);
      } catch (e) {
        const msg = e instanceof UnsafeUrlError ? e.message : "Invalid endpoint URL.";
        return NextResponse.json({ error: msg }, { status: 400 });
      }
    }
    const provider = await prisma.modelProvider.create({
      data: {
        tenantId: session.tenantId,
        name,
        type,
        endpoint: endpoint || null,
        apiKeyEncrypted: encrypt(apiKey),
        defaultModel,
      },
    });
    return NextResponse.json({ ...provider, apiKeyEncrypted: maskApiKey(provider.apiKeyEncrypted) }, { status: 201 });
  });
}
