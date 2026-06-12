// #255: list 路由统一分页参数解析。响应信封 {items, total, skip, take}。
// take 默认即旧上限（行为兼容：不传参数 = 原来的前 200 条），skip 翻页。

export interface PageParams {
  skip: number;
  take: number;
}

export function parsePageParams(
  url: URL,
  opts: { defaultTake?: number; maxTake?: number } = {}
): PageParams | { error: string } {
  const maxTake = opts.maxTake ?? 200;
  const defaultTake = opts.defaultTake ?? maxTake;
  const rawSkip = url.searchParams.get("skip") ?? "0";
  const rawTake = url.searchParams.get("take") ?? String(defaultTake);
  const skip = Number.parseInt(rawSkip, 10);
  const take = Number.parseInt(rawTake, 10);
  // 上界防 Prisma Int 溢出 500（深翻页本身 O(skip)，量级见 #255 取舍）
  if (!Number.isInteger(skip) || skip < 0 || skip > 1_000_000 || String(skip) !== rawSkip.trim()) {
    return { error: "skip 必须是 0–1000000 的整数" };
  }
  if (!Number.isInteger(take) || take < 1 || take > maxTake || String(take) !== rawTake.trim()) {
    return { error: `take 必须是 1–${maxTake} 的整数` };
  }
  return { skip, take };
}
