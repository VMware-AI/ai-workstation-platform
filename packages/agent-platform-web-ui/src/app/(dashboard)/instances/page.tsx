"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Plus, Cpu, Play, Square, RotateCcw, Trash2, ExternalLink } from "lucide-react";

interface Instance {
  id: string;
  name: string;
  status: string;
  endpoint: string | null;
  ipAddress: string | null;
  vmRefId: string | null;
  createdAt: string;
  computePool: { name: string; type: string } | null;
}

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

export default function InstancesPage() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // 5s 轮询会整表重取——记录用户已翻到的深度，刷新时把已加载的页全部
  // 重取再覆盖，否则"加载更多"的结果会被下一次轮询冲掉（#255 review）。
  const loadedTargetRef = useRef(200);

  // Out-of-order guard (#357 item 1): the 5s poll fires serial reloads with no
  // in-flight lock, so a slow response can land after a newer one and overwrite
  // fresh data with stale rows. Tag each load with a generation; only the most
  // recent caller is allowed to commit its result to state.
  const loadGenRef = useRef(0);

  const load = useCallback(async () => {
    const gen = ++loadGenRef.current;
    const pages = Math.ceil(loadedTargetRef.current / 200);
    const all: Instance[] = [];
    let totalCount = 0;
    for (let i = 0; i < pages; i++) {
      const res = await fetch(`/api/instances?skip=${i * 200}`);
      if (!res.ok) return;
      const d = await res.json();
      all.push(...(d.items ?? []));
      totalCount = d.total ?? 0;
      if (all.length >= totalCount) break;
    }
    // A newer load started while we were awaiting — drop this stale result.
    if (gen !== loadGenRef.current) return;
    setInstances(all);
    setTotal(totalCount);
  }, []);

  async function loadMore() {
    loadedTargetRef.current = instances.length + 200;
    await load();
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  async function handleAction(id: string, action: string) {
    setLoading(id + action);
    setActionError(null);
    try {
      let res: Response;
      if (action === "delete") {
        if (!confirm("确认删除此实例？")) return;
        res = await fetch(`/api/instances/${id}`, { method: "DELETE" });
      } else {
        res = await fetch(`/api/instances/${id}?action=${action}`, { method: "POST" });
      }
      if (!res.ok) {
        // Surface lifecycle conflicts (409, #236) instead of silently dropping.
        const d = await res.json().catch(() => null);
        setActionError(d?.error || "操作失败，请稍后重试");
      }
    } catch {
      // Network failure must not leave the button disabled forever.
      setActionError("网络错误，请稍后重试");
    } finally {
      setLoading(null);
    }
    load();
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">实例管理</h1>
        <Link href="/instances/deploy">
          <Button><Plus className="h-4 w-4 mr-2" />从模板创建实例</Button>
        </Link>
      </div>

      {actionError && (
        <p role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {actionError}
        </p>
      )}

      {instances.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-gray-400">
            <Cpu className="h-10 w-10 mb-3" />
            <p>暂无实例</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {instances.map(inst => (
            <Card key={inst.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <div className="h-10 w-10 rounded-full bg-green-100 flex items-center justify-center shrink-0">
                    <Cpu className="h-5 w-5 text-green-600" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-gray-900">{inst.name}</p>
                      <Badge variant={statusColors[inst.status] || "secondary"}>{inst.status}</Badge>
                    </div>
                    <p className="text-sm text-gray-500">
                      vSphere 实例
                      {inst.computePool && <span className="ml-2 text-xs text-blue-500">[{inst.computePool.name}]</span>}
                    </p>
                    {(inst.endpoint || inst.ipAddress) && (
                      <p className="text-xs text-gray-400 flex items-center gap-1 mt-0.5">
                        <ExternalLink className="h-3 w-3" />
                        {inst.ipAddress ? `${inst.ipAddress} · ` : ""}{inst.endpoint}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex gap-2 ml-4 shrink-0">
                  <Link href={`/instances/${inst.id}`}>
                    <Button variant="outline" size="sm">详情/调用</Button>
                  </Link>
                  {inst.status === "STOPPED" && (
                    <Button size="sm" variant="outline" onClick={() => handleAction(inst.id, "start")} disabled={loading === inst.id + "start"}>
                      <Play className="h-4 w-4" />
                    </Button>
                  )}
                  {inst.status === "RUNNING" && (
                    <>
                      <Button size="sm" variant="outline" onClick={() => handleAction(inst.id, "stop")} disabled={loading === inst.id + "stop"}>
                        <Square className="h-4 w-4" />
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => handleAction(inst.id, "restart")} disabled={loading === inst.id + "restart"}>
                        <RotateCcw className="h-4 w-4" />
                      </Button>
                    </>
                  )}
                  {/* Escape hatch (#249): a start IIFE that died with the
                      server strands the row in INITIALIZING — stop is allowed
                      from it (API #236), so surface the button instead of
                      making users wait for the 30-min reaper. */}
                  {inst.status === "INITIALIZING" && (
                    <Button size="sm" variant="outline" onClick={() => handleAction(inst.id, "stop")} disabled={loading === inst.id + "stop"}>
                      <Square className="h-4 w-4" />
                    </Button>
                  )}
                  {/* Recovery path (#236): the reaper's manual-verify marks
                      instances ERROR while the VM itself may be healthy —
                      offer restart instead of forcing a raw API call. */}
                  {inst.status === "ERROR" && inst.vmRefId && (
                    <Button size="sm" variant="outline" onClick={() => handleAction(inst.id, "restart")} disabled={loading === inst.id + "restart"}>
                      <RotateCcw className="h-4 w-4" />
                    </Button>
                  )}
                  <Button size="sm" variant="destructive" onClick={() => handleAction(inst.id, "delete")} disabled={loading === inst.id + "delete"}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {instances.length < total && (
        <div className="flex justify-center py-2">
          <Button variant="outline" onClick={loadMore}>
            加载更多（已显示 {instances.length}/{total}）
          </Button>
        </div>
      )}
    </div>
  );
}
