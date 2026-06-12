import { NextResponse } from "next/server";
import { logger } from "@/lib/logger";

// Convert an unexpected compute-pool save/encrypt/persist failure into a
// structured 500 the UI can render — instead of an uncaught throw that returns
// an empty body and crashes the client's `res.json()`. The crypto layer's
// ENCRYPTION_KEY message is safe and actionable, so surface it; anything else
// stays generic, with full detail going to the server log.
export function poolSaveErrorResponse(e: unknown, requestId?: string): NextResponse {
  logger.error("compute-pools save failed", {
    scope: "compute-pools",
    error: e instanceof Error ? e.message : String(e),
    ...(requestId ? { requestId } : {}),
  });
  const raw = e instanceof Error ? e.message : "";
  const msg = raw.includes("ENCRYPTION_KEY")
    ? "服务器加密未配置：请在服务端 .env 设置 ENCRYPTION_KEY（openssl rand -hex 32）后重启服务。"
    : "保存失败：服务器内部错误，请查看服务端日志。";
  return NextResponse.json({ error: msg }, { status: 500 });
}
