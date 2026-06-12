"use client";
import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
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

  const chartData = historical.map(m => ({
    time: new Date(m.timestamp).toLocaleTimeString(),
    CPU: m.cpuPercent,
    内存: m.memoryMb,
  }));

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

      {chartData.length > 0 ? (
        <div className="grid gap-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">CPU 历史趋势</CardTitle></CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="time" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} unit="%" />
                  <Tooltip />
                  <Line type="monotone" dataKey="CPU" stroke="#3b82f6" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">内存历史趋势</CardTitle></CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="time" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} unit=" MB" />
                  <Tooltip />
                  <Line type="monotone" dataKey="内存" stroke="#8b5cf6" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      ) : (
        <p className="text-gray-500 text-sm">{selectedId ? "暂无指标数据" : "请先选择实例"}</p>
      )}
    </div>
  );
}
