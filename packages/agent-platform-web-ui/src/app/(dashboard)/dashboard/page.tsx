import Link from "next/link";
import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Cpu, FileCode2, Zap, TrendingUp, Server, AlertTriangle } from "lucide-react";
import { getSession } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

const statusColors: Record<string, "success" | "warning" | "destructive" | "secondary" | "default"> = {
  RUNNING: "success",
  PENDING: "warning",
  PROVISIONING: "warning",
  INITIALIZING: "warning",
  STOPPING: "warning",
  STOPPED: "secondary",
  ERROR: "destructive",
  DELETED: "secondary",
};

function fmtTokens(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function fmtCost(cents: number) {
  return "$" + (cents / 100).toFixed(2);
}

export default async function DashboardPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const tenantId = session.tenantId;
  const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  // Server component: evaluated once per request, not a reactive render — the
  // purity rule's "unstable across re-renders" concern does not apply here.
  // eslint-disable-next-line react-hooks/purity
  const dayStart = new Date(Date.now() - 24 * 60 * 60 * 1000);

  const [instanceCounts, poolCount, monthUsage, dayUsage, recentInstances, tenant] =
    await Promise.all([
      prisma.instance.groupBy({
        by: ["status"],
        where: { tenantId, status: { not: "DELETED" } },
        _count: true,
      }),
      prisma.computePool.count({ where: { tenantId, enabled: true } }),
      prisma.usageRecord.aggregate({
        where: { tenantId, timestamp: { gte: monthStart } },
        _sum: { totalTokens: true, costCents: true },
        _count: true,
      }),
      prisma.usageRecord.aggregate({
        where: { tenantId, timestamp: { gte: dayStart } },
        _sum: { totalTokens: true, costCents: true },
        _count: true,
      }),
      prisma.instance.findMany({
        where: { tenantId, status: { not: "DELETED" } },
        orderBy: { createdAt: "desc" },
        take: 5,
      }),
      prisma.tenant.findUnique({ where: { id: tenantId } }),
    ]);

  const statusMap = Object.fromEntries(instanceCounts.map((g) => [g.status, g._count]));
  const totalInstances = Object.values(statusMap).reduce((a, b) => a + b, 0);
  const runningInstances = statusMap["RUNNING"] ?? 0;
  const provisioningInstances = (statusMap["PROVISIONING"] ?? 0) + (statusMap["INITIALIZING"] ?? 0);
  const errorInstances = statusMap["ERROR"] ?? 0;

  const monthTokens = monthUsage._sum.totalTokens ?? 0;
  const quotaTokens = tenant ? Number(tenant.quotaTokensMonth) : 1_000_000;
  const quotaUsedPct = Math.min(100, Math.round((monthTokens / quotaTokens) * 100));
  const monthCostCents = monthUsage._sum.costCents ?? 0;
  const dayTokens = dayUsage._sum.totalTokens ?? 0;
  const dayCostCents = dayUsage._sum.costCents ?? 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">总览</h1>
      </div>

      {/* Top stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500">实例运行</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <Cpu className="h-5 w-5 text-green-600" />
              <span className="text-2xl font-bold text-gray-900">{runningInstances}</span>
              <span className="text-sm text-gray-400">/ {totalInstances}</span>
            </div>
            {provisioningInstances > 0 && (
              <p className="text-xs text-blue-500 mt-1">{provisioningInstances} 台制备中</p>
            )}
            {errorInstances > 0 && (
              <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" />{errorInstances} 台异常
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500">计算池</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5 text-blue-600" />
              <span className="text-2xl font-bold text-gray-900">{poolCount}</span>
            </div>
            <p className="text-xs text-gray-400 mt-1">已启用的资源池</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500">今日用量</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <Zap className="h-5 w-5 text-purple-600" />
              <span className="text-2xl font-bold text-gray-900">{fmtTokens(dayTokens)}</span>
            </div>
            <p className="text-xs text-gray-400 mt-1">{dayUsage._count} 次调用 · {fmtCost(dayCostCents)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500">月度 Token</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-orange-600" />
              <span className="text-2xl font-bold text-gray-900">{fmtTokens(monthTokens)}</span>
            </div>
            <div className="mt-2">
              <div className="flex justify-between text-xs text-gray-400 mb-1">
                <span>{quotaUsedPct}% 已用</span>
                <span>上限 {fmtTokens(quotaTokens)}</span>
              </div>
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    quotaUsedPct >= 90 ? "bg-red-500" : quotaUsedPct >= 70 ? "bg-yellow-400" : "bg-blue-500"
                  }`}
                  style={{ width: `${quotaUsedPct}%` }}
                />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Instance status breakdown */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">实例状态分布</CardTitle>
          </CardHeader>
          <CardContent>
            {Object.entries(statusMap).length === 0 ? (
              <p className="text-sm text-gray-400">暂无实例</p>
            ) : (
              <div className="space-y-2">
                {Object.entries(statusMap).map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between">
                    <Badge variant={statusColors[status] || "secondary"}>{status}</Badge>
                    <div className="flex items-center gap-2 flex-1 mx-3">
                      <div className="h-1.5 bg-gray-100 rounded-full flex-1 overflow-hidden">
                        <div
                          className="h-full bg-blue-400 rounded-full"
                          style={{ width: `${totalInstances > 0 ? (count / totalInstances) * 100 : 0}%` }}
                        />
                      </div>
                    </div>
                    <span className="text-sm font-medium text-gray-700 w-6 text-right">{count}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Recent instances */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">最近实例</CardTitle>
              <Link href="/instances" className="text-xs text-blue-500 hover:underline">
                查看全部
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            {recentInstances.length === 0 ? (
              <div className="text-center py-6 text-gray-400">
                <Cpu className="h-8 w-8 mx-auto mb-2 opacity-40" />
                <p className="text-sm">
                  暂无实例，<Link href="/instances" className="text-blue-500">立即创建</Link>
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {recentInstances.map((inst) => (
                  <Link key={inst.id} href={`/instances/${inst.id}`}>
                    <div className="flex items-center justify-between p-2 rounded-md hover:bg-gray-50 transition-colors">
                      <div>
                        <p className="text-sm font-medium text-gray-800">{inst.name}</p>
                        <p className="text-xs text-gray-400">{inst.status}</p>
                      </div>
                      <Badge variant={statusColors[inst.status] || "secondary"}>{inst.status}</Badge>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Quick start */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <FileCode2 className="h-4 w-4" />快速开始
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { step: "1", title: "配置计算池", desc: "添加 vSphere 资源池并测试 vCenter 连接", href: "/compute-pools" },
                { step: "2", title: "从模板创建", desc: "选 VC 模板、填 cloud-init（单台或 CSV 批量）部署实例", href: "/instances/deploy" },
                { step: "3", title: "管理实例", desc: "查看状态、启停、调用已部署的 Agent VM", href: "/instances" },
              ].map((item) => (
                <Link key={item.step} href={item.href}>
                  <div className="border rounded-lg p-3 hover:border-blue-400 hover:bg-blue-50 transition-colors cursor-pointer">
                    <div className="text-xs font-bold text-blue-600 mb-1">STEP {item.step}</div>
                    <div className="text-sm font-semibold text-gray-800 mb-1">{item.title}</div>
                    <div className="text-xs text-gray-500">{item.desc}</div>
                  </div>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      <p className="text-xs text-gray-400 mt-4 text-right">
        月度费用合计：{fmtCost(monthCostCents)} · {monthUsage._count} 次调用
      </p>
    </div>
  );
}
