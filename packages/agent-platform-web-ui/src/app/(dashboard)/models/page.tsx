"use client";
import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Plus, Zap, Trash2, TestTube2 } from "lucide-react";
import { fetchJson } from "@/lib/fetchJson";

interface Provider {
  id: string;
  name: string;
  type: string;
  endpoint: string | null;
  apiKeyEncrypted: string;
  defaultModel: string;
  enabled: boolean;
}

export default function ModelsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [total, setTotal] = useState(0);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", type: "openai", endpoint: "", apiKey: "", defaultModel: "" });
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id: string; ok: boolean; message: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { load(); }, []);

  async function load() {
    const r = await fetchJson<{ items?: Provider[]; total?: number }>("/api/model-providers");
    if (r.ok) {
      setProviders(r.data.items ?? []);
      setTotal(r.data.total ?? 0);
    }
  }

  async function loadMore() {
    const r = await fetchJson<{ items?: Provider[]; total?: number }>(
      `/api/model-providers?skip=${providers.length}`
    );
    if (r.ok) {
      setProviders((prev) => [...prev, ...(r.data.items ?? [])]);
      setTotal(r.data.total ?? 0);
    }
  }

  async function handleCreate() {
    setCreating(true);
    setError("");
    try {
      const r = await fetchJson("/api/model-providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (r.ok) {
        setOpen(false);
        setForm({ name: "", type: "openai", endpoint: "", apiKey: "", defaultModel: "" });
        load();
      } else {
        setError(r.error || "创建失败");
      }
    } finally {
      // Always re-enable the create button, even on a network error.
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("确认删除此 Provider？")) return;
    const r = await fetchJson(`/api/model-providers/${id}`, { method: "DELETE" });
    if (!r.ok) {
      setError(r.error || "删除失败");
      return;
    }
    load();
  }

  async function handleTest(id: string) {
    setTesting(id);
    setTestResult(null);
    try {
      // The test endpoint returns { ok, message } (with 200 or 400); read the
      // body on a 2xx, fall back to the helper's message on a hard failure.
      const r = await fetchJson<{ ok?: boolean; message?: string }>(
        `/api/model-providers/${id}`,
        { method: "POST" }
      );
      if (r.ok) {
        setTestResult({ id, ok: Boolean(r.data.ok), message: r.data.message ?? "" });
      } else {
        setTestResult({ id, ok: false, message: r.error });
      }
    } finally {
      setTesting(null);
    }
  }

  const typeLabels: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", azure: "Azure", custom: "自定义" };
  const defaultModels: Record<string, string> = { openai: "gpt-4o", anthropic: "claude-opus-4-7", azure: "gpt-4o", custom: "" };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">模型接入管理</h1>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button><Plus className="h-4 w-4 mr-2" />添加 Provider</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>添加模型 Provider</DialogTitle></DialogHeader>
            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium text-gray-700">名称</label>
                <Input placeholder="如：我的 OpenAI" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700">类型</label>
                <Select value={form.type} onValueChange={v => setForm({ ...form, type: v, defaultModel: defaultModels[v] || "" })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="openai">OpenAI</SelectItem>
                    <SelectItem value="anthropic">Anthropic</SelectItem>
                    <SelectItem value="azure">Azure OpenAI</SelectItem>
                    <SelectItem value="custom">自定义</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {(form.type === "azure" || form.type === "custom") && (
                <div>
                  <label className="text-sm font-medium text-gray-700">API Endpoint</label>
                  <Input placeholder="https://your-endpoint" value={form.endpoint} onChange={e => setForm({ ...form, endpoint: e.target.value })} />
                </div>
              )}
              <div>
                <label className="text-sm font-medium text-gray-700">API Key</label>
                <Input type="password" placeholder="sk-..." value={form.apiKey} onChange={e => setForm({ ...form, apiKey: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700">默认模型</label>
                <Input placeholder={defaultModels[form.type] || "model-name"} value={form.defaultModel} onChange={e => setForm({ ...form, defaultModel: e.target.value })} />
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
              <Button onClick={handleCreate} disabled={creating}>{creating ? "创建中..." : "创建"}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {providers.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-gray-400">
            <Zap className="h-10 w-10 mb-3" />
            <p>暂无 Provider，请先添加</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {providers.map(p => (
            <Card key={p.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-full bg-blue-100 flex items-center justify-center">
                    <Zap className="h-5 w-5 text-blue-600" />
                  </div>
                  <div>
                    <p className="font-medium text-gray-900">{p.name}</p>
                    <p className="text-sm text-gray-500">{typeLabels[p.type]} · {p.defaultModel}</p>
                    {p.endpoint && <p className="text-xs text-gray-400">{p.endpoint}</p>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {testResult?.id === p.id && (
                    <Badge variant={testResult.ok ? "success" : "destructive"}>{testResult.message}</Badge>
                  )}
                  <Badge variant={p.enabled ? "success" : "secondary"}>{p.enabled ? "启用" : "禁用"}</Badge>
                  <Button variant="outline" size="sm" onClick={() => handleTest(p.id)} disabled={testing === p.id}>
                    <TestTube2 className="h-4 w-4 mr-1" />{testing === p.id ? "测试中..." : "测试"}
                  </Button>
                  <Button variant="destructive" size="sm" onClick={() => handleDelete(p.id)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {providers.length < total && (
        <div className="flex justify-center py-2">
          <Button variant="outline" onClick={loadMore}>
            加载更多（已显示 {providers.length}/{total}）
          </Button>
        </div>
      )}
    </div>
  );
}
