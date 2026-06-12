"use client";
import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";

interface Instance {
  id: string;
  name: string;
  status: string;
  endpoint: string | null;
  ipAddress: string | null;
  vmRefId: string | null;
  startedAt: string | null;
  errorMessage: string | null;
  computePool: { name: string; type: string } | null;
}

interface LogEntry { id: string; timestamp: string; level: string; message: string }
interface Metric { id: string; timestamp: string; cpuPercent: number; memoryMb: number }
interface ProvisionEvent { id: string; event: string; message: string; createdAt: string }

const statusColors: Record<string, string> = {
  RUNNING: "text-green-600",
  STOPPED: "text-gray-500",
  ERROR: "text-red-600",
  PENDING: "text-yellow-600",
  PROVISIONING: "text-blue-600",
  INITIALIZING: "text-blue-600",
  STOPPING: "text-yellow-600",
};

export default function InstanceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [instance, setInstance] = useState<Instance | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [containerLogs, setContainerLogs] = useState<string[]>([]);
  const [metrics, setMetrics] = useState<{ current: { cpuPercent: number; memoryMb: number } | null }>({ current: null });
  const [events, setEvents] = useState<ProvisionEvent[]>([]);
  const [tab, setTab] = useState<"logs" | "metrics" | "events">("logs");

  const load = useCallback(async () => {
    const res = await fetch(`/api/instances/${id}`);
    if (res.ok) setInstance(await res.json());
  }, [id]);

  const loadLogs = useCallback(async () => {
    const res = await fetch(`/api/instances/${id}/logs`);
    if (res.ok) {
      const data = await res.json();
      setLogs(data.dbLogs || []);
      setContainerLogs(data.containerLogs || []);
    }
  }, [id]);

  const loadMetrics = useCallback(async () => {
    const res = await fetch(`/api/instances/${id}/metrics`);
    if (res.ok) setMetrics(await res.json());
  }, [id]);

  const loadEvents = useCallback(async () => {
    const res = await fetch(`/api/instances/${id}/events`);
    if (res.ok) setEvents(await res.json());
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (tab === "logs") loadLogs();
    if (tab === "metrics") loadMetrics();
    if (tab === "events") loadEvents();
  }, [tab, loadLogs, loadMetrics, loadEvents]);

  if (!instance) return <div className="text-gray-500">加载中...</div>;

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{instance.name}</h1>
          <p className="text-sm text-gray-500">
            vSphere 实例
            {instance.computePool && <span className="ml-2 text-blue-500">[{instance.computePool.name} / {instance.computePool.type}]</span>}
          </p>
          {instance.ipAddress && <p className="text-xs text-gray-400 mt-0.5">IP: {instance.ipAddress}{instance.vmRefId ? ` · VM: ${instance.vmRefId}` : ""}</p>}
          {instance.errorMessage && <p className="text-xs text-red-500 mt-0.5">{instance.errorMessage}</p>}
        </div>
        <div className="flex items-center gap-2">
          <span className={`font-medium ${statusColors[instance.status] || "text-gray-500"}`}>● {instance.status}</span>
          <Button variant="outline" size="sm" onClick={load}><RefreshCw className="h-4 w-4" /></Button>
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        {(["logs", "metrics", "events"] as const).map(t => (
          <Button key={t} variant={tab === t ? "default" : "outline"} size="sm" onClick={() => setTab(t)}>
            {{ logs: "日志", metrics: "监控", events: "制备事件" }[t]}
          </Button>
        ))}
      </div>

      {tab === "logs" && (
        <Card>
          <CardContent className="p-4">
            <Button size="sm" variant="outline" className="mb-3" onClick={loadLogs}>刷新日志</Button>
            <div className="bg-gray-900 rounded p-3 font-mono text-xs text-green-400 max-h-96 overflow-y-auto space-y-1">
              {containerLogs.length === 0 && logs.length === 0 && <p className="text-gray-500">暂无日志</p>}
              {containerLogs.map((l, i) => <p key={i}>{l}</p>)}
              {logs.map(l => (
                <p key={l.id}>
                  <span className="text-gray-500">{new Date(l.timestamp).toLocaleTimeString()}</span>
                  {" "}<span className={l.level === "error" ? "text-red-400" : "text-green-400"}>[{l.level}]</span>
                  {" "}{l.message}
                </p>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {tab === "metrics" && (
        <div className="space-y-4">
          <Button size="sm" variant="outline" onClick={loadMetrics}>刷新指标</Button>
          {metrics.current ? (
            <div className="grid grid-cols-2 gap-4">
              <Card>
                <CardHeader><CardTitle className="text-sm">CPU 使用率</CardTitle></CardHeader>
                <CardContent>
                  <div className="text-3xl font-bold text-blue-600">{metrics.current.cpuPercent.toFixed(1)}%</div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-sm">内存使用</CardTitle></CardHeader>
                <CardContent>
                  <div className="text-3xl font-bold text-purple-600">{metrics.current.memoryMb.toFixed(0)} MB</div>
                </CardContent>
              </Card>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">实例未运行或无指标数据</p>
          )}
        </div>
      )}

      {tab === "events" && (
        <Card>
          <CardContent className="p-4">
            <Button size="sm" variant="outline" className="mb-4" onClick={loadEvents}>刷新</Button>
            {events.length === 0 ? (
              <p className="text-sm text-gray-400">暂无制备事件</p>
            ) : (
              <ol className="relative border-l border-gray-200 ml-3 space-y-4">
                {events.map((e) => (
                  <li key={e.id} className="ml-4">
                    <div className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border border-white bg-blue-400" />
                    <time className="text-xs text-gray-400">{new Date(e.createdAt).toLocaleString()}</time>
                    <p className="text-sm font-medium text-gray-800">{e.event}</p>
                    <p className="text-xs text-gray-500">{e.message}</p>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
