"use client";
import { useState, useEffect, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";
import { EChart } from "@/components/ui/echart";
import { buildTimeSeriesLineOption } from "@/lib/charts/lineOption";
import { fetchJson } from "@/lib/fetchJson";

interface Instance { id: string; name: string }
interface Metric { timestamp: string; cpuPercent: number; memoryMb: number }

export default function MonitoringPage() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [historical, setHistorical] = useState<Metric[]>([]);
  const [current, setCurrent] = useState<{ cpuPercent: number; memoryMb: number } | null>(null);

  useEffect(() => {
    // 401/500 returns { error } — guard so it never lands in `instances` and
    // crashes the .map below (#357 item 3).
    fetchJson<{ items?: Instance[] }>("/api/instances").then((r) => {
      if (r.ok) setInstances(r.data.items ?? []);
    });
  }, []);

  async function loadMetrics() {
    if (!selectedId) return;
    const r = await fetchJson<{ historical?: Metric[]; current?: { cpuPercent: number; memoryMb: number } }>(
      `/api/instances/${selectedId}/metrics?hours=1`
    );
    if (r.ok) {
      setHistorical(r.data.historical ?? []);
      setCurrent(r.data.current ?? null);
    }
  }

  useEffect(() => { loadMetrics(); }, [selectedId]);

  const times = useMemo(
    () => historical.map(m => new Date(m.timestamp).toLocaleTimeString()),
    [historical]
  );
  const cpuOption = useMemo(
    () => buildTimeSeriesLineOption({
      categories: times,
      name: "CPU",
      values: historical.map(m => m.cpuPercent),
      color: "#3b82f6",
      unit: "%",
    }),
    [times, historical]
  );
  const memOption = useMemo(
    () => buildTimeSeriesLineOption({
      categories: times,
      name: "内存",
      values: historical.map(m => m.memoryMb),
      color: "#8b5cf6",
      unit: " MB",
    }),
    [times, historical]
  );

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">监控</h1>
      <div className="flex gap-3 mb-4">
        <div className="w-64">
          <Select value={selectedId} onValueChange={setSelectedId}>
            <SelectTrigger><SelectValue placeholder="选择实例" /></SelectTrigger>
            <SelectContent>
              {instances.map(i => <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" size="sm" onClick={loadMetrics}><RefreshCw className="h-4 w-4" /></Button>
      </div>

      {current && (
        <div className="grid grid-cols-2 gap-4 mb-6">
          <Card>
            <CardHeader><CardTitle className="text-sm text-gray-500">实时 CPU</CardTitle></CardHeader>
            <CardContent>
              <div className="text-3xl font-bold text-blue-600">{current.cpuPercent.toFixed(1)}%</div>
              <div className="mt-2 h-2 bg-gray-200 rounded">
                <div className="h-2 bg-blue-500 rounded" style={{ width: `${Math.min(current.cpuPercent, 100)}%` }} />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm text-gray-500">实时内存</CardTitle></CardHeader>
            <CardContent>
              <div className="text-3xl font-bold text-purple-600">{current.memoryMb.toFixed(0)} MB</div>
              <div className="mt-2 h-2 bg-gray-200 rounded">
                <div className="h-2 bg-purple-500 rounded" style={{ width: `${Math.min((current.memoryMb / 512) * 100, 100)}%` }} />
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {historical.length > 0 ? (
        <div className="grid gap-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">CPU 历史趋势</CardTitle></CardHeader>
            <CardContent>
              <EChart height={200} option={cpuOption} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">内存历史趋势</CardTitle></CardHeader>
            <CardContent>
              <EChart height={200} option={memOption} />
            </CardContent>
          </Card>
        </div>
      ) : (
        <p className="text-gray-500 text-sm">{selectedId ? "暂无指标数据" : "请先选择实例"}</p>
      )}
    </div>
  );
}
