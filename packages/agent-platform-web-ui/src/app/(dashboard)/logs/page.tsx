"use client";
import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";
import { fetchJson } from "@/lib/fetchJson";

interface Instance { id: string; name: string }
interface LogEntry { id: string; timestamp: string; level: string; message: string }

export default function LogsPage() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [dbLogs, setDbLogs] = useState<LogEntry[]>([]);
  const [containerLogs, setContainerLogs] = useState<string[]>([]);

  useEffect(() => {
    // 401/500 returns { error } — guard so it never lands in `instances` and
    // crashes the .map below (#357 item 3).
    fetchJson<{ items?: Instance[] }>("/api/instances").then((r) => {
      if (r.ok) setInstances(r.data.items ?? []);
    });
  }, []);

  async function loadLogs() {
    if (!selectedId) return;
    const r = await fetchJson<{ dbLogs?: LogEntry[]; containerLogs?: string[] }>(
      `/api/instances/${selectedId}/logs`
    );
    if (r.ok) {
      setDbLogs(r.data.dbLogs ?? []);
      setContainerLogs(r.data.containerLogs ?? []);
    }
  }

  useEffect(() => { loadLogs(); }, [selectedId]);

  const levelColor = (level: string) => {
    if (level === "error") return "text-red-400";
    if (level === "warn") return "text-yellow-400";
    return "text-green-400";
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">实例日志</h1>
      <div className="flex gap-3 mb-4">
        <div className="w-64">
          <Select value={selectedId} onValueChange={setSelectedId}>
            <SelectTrigger><SelectValue placeholder="选择实例" /></SelectTrigger>
            <SelectContent>
              {instances.map(i => <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" size="sm" onClick={loadLogs}><RefreshCw className="h-4 w-4" /></Button>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-sm">日志输出</CardTitle></CardHeader>
        <CardContent>
          <div className="bg-gray-900 rounded p-4 font-mono text-xs max-h-[60vh] overflow-y-auto space-y-1">
            {containerLogs.length === 0 && dbLogs.length === 0 && (
              <p className="text-gray-500">{selectedId ? "暂无日志" : "请先选择实例"}</p>
            )}
            {containerLogs.map((l, i) => <p key={i} className="text-gray-300">{l}</p>)}
            {dbLogs.map(l => (
              <p key={l.id}>
                <span className="text-gray-500">{new Date(l.timestamp).toLocaleString()}</span>
                {" "}<span className={levelColor(l.level)}>[{l.level.toUpperCase()}]</span>
                {" "}<span className="text-gray-200">{l.message}</span>
              </p>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
