"use client";
import { useState, useEffect, useCallback } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { Plus, Server, Trash2, Edit2, Check, X } from "lucide-react";
import { fetchJson } from "@/lib/fetchJson";

interface ComputePool {
  id: string;
  name: string;
  type: "docker" | "vsphere";
  enabled: boolean;
  config: Record<string, string>;
  createdAt: string;
}

// A pool stores ONLY the vCenter connection. Template / datastore / network /
// resource pool / folder are chosen at deploy time from the pool's live
// inventory (doc 33 §6.5), so they are NOT collected here.
const EMPTY_VSPHERE = {
  host: "",
  username: "",
  password: "",
  datacenter: "",
};

export default function ComputePoolsPage() {
  const [pools, setPools] = useState<ComputePool[]>([]);
  const [total, setTotal] = useState(0);
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", type: "vsphere" as const, config: EMPTY_VSPHERE });
  // Skip TLS cert verification — needed for self-signed lab vCenters. Stored as
  // config.verifySsl=false; both "测试 VC" and provisioning honor it.
  const [skipTlsVerify, setSkipTlsVerify] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [invPoolId, setInvPoolId] = useState<string | null>(null);
  const [invLoading, setInvLoading] = useState(false);
  const [invData, setInvData] = useState<{ ok: boolean; error?: string; inventory?: Record<string, string[]> } | null>(null);

  const load = useCallback(async () => {
    const r = await fetchJson<{ items?: ComputePool[]; total?: number }>("/api/compute-pools");
    if (r.ok) {
      setPools(r.data.items ?? []);
      setTotal(r.data.total ?? 0);
    }
  }, []);

  async function loadMore() {
    const r = await fetchJson<{ items?: ComputePool[]; total?: number }>(
      `/api/compute-pools?skip=${pools.length}`
    );
    if (r.ok) {
      setPools((prev) => [...prev, ...(r.data.items ?? [])]);
      setTotal(r.data.total ?? 0);
    }
  }

  useEffect(() => { load(); }, [load]);

  function openCreate() {
    setEditId(null);
    setForm({ name: "", type: "vsphere", config: EMPTY_VSPHERE });
    setSkipTlsVerify(false);
    setError("");
    setTestResult(null);
    setOpen(true);
  }

  function openEdit(pool: ComputePool) {
    setEditId(pool.id);
    // Server returns config.password as a sentinel "****" when a password
    // exists, never the real value. Clear the password input so the user
    // sees an empty field and only types a new value if they want to
    // rotate it; leaving it blank keeps the current encrypted password.
    const poolCfg = (pool.config ?? {}) as Record<string, unknown>;
    setSkipTlsVerify(poolCfg.verifySsl === false);
    const { password: _omit, verifySsl: _vs, ...restConfig } = poolCfg;
    void _omit;
    void _vs;
    setForm({
      name: pool.name,
      // Legacy docker pools (pre doc 33 §7) edit as vsphere — docker is no
      // longer creatable (#90 M14) and the form only carries vsphere config.
      type: "vsphere",
      config: { ...EMPTY_VSPHERE, ...(restConfig as Record<string, string>), password: "" },
    });
    setError("");
    setTestResult(null);
    setOpen(true);
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await fetchJson<{ ok?: boolean; error?: string }>("/api/compute-pools/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host: form.config.host,
          username: form.config.username,
          password: form.config.password,
          verifySsl: skipTlsVerify ? false : undefined,
        }),
      });
      // The test endpoint returns its own { ok, error } envelope (200 even on a
      // failed connection), so read the body on success and the helper's error
      // message otherwise.
      if (r.ok) setTestResult({ ok: Boolean(r.data.ok), error: r.data.error });
      else setTestResult({ ok: false, error: r.error });
    } finally {
      setTesting(false);
    }
  }

  async function handleInventory(poolId: string) {
    setInvPoolId(poolId);
    setInvLoading(true);
    setInvData(null);
    try {
      const r = await fetchJson<{ ok?: boolean; error?: string; inventory?: Record<string, string[]> }>(
        `/api/compute-pools/${poolId}/inventory`,
        { method: "POST", headers: { "Content-Type": "application/json" } }
      );
      if (r.ok) setInvData({ ok: Boolean(r.data.ok), error: r.data.error, inventory: r.data.inventory });
      else setInvData({ ok: false, error: r.error });
    } finally {
      setInvLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    const body: Record<string, unknown> = { name: form.name, type: form.type };
    if (form.type === "vsphere") {
      const cfg: Record<string, unknown> = {};
      (["host", "username", "password", "datacenter"] as const).forEach((k) => {
        if (form.config[k]) cfg[k] = form.config[k];
      });
      // On create, password is required. On edit, an empty password means
      // "keep the existing one" — omit it so the server preserves the
      // previously-encrypted value rather than overwriting it.
      if (editId && !form.config.password) {
        delete cfg.password;
      }
      // Only persist when skipping — the backend treats verifySsl===false as
      // "insecure"; absent/true both mean verify, so we omit it otherwise.
      if (skipTlsVerify) cfg.verifySsl = false;
      body.config = cfg;
    }

    try {
      const url = editId ? `/api/compute-pools/${editId}` : "/api/compute-pools";
      const method = editId ? "PATCH" : "POST";
      const r = await fetchJson(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        setError(r.error);
        return;
      }
      setOpen(false);
      load();
    } finally {
      // Always clear the spinner — a network failure must not freeze the button.
      setSaving(false);
    }
  }

  async function handleToggle(pool: ComputePool) {
    const r = await fetchJson(`/api/compute-pools/${pool.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !pool.enabled }),
    });
    if (!r.ok) {
      setError(r.error || "操作失败");
      return;
    }
    load();
  }

  async function handleDelete(id: string) {
    if (!confirm("确认删除此计算池？")) return;
    setDeleting(id);
    try {
      const r = await fetchJson(`/api/compute-pools/${id}`, { method: "DELETE" });
      if (!r.ok) {
        setError(r.error || "删除失败");
        return;
      }
      load();
    } finally {
      // Always re-enable the delete button, even on a network error.
      setDeleting(null);
    }
  }

  const cfg = form.config;
  // Any connection-field edit invalidates a prior successful test, so the user
  // must re-test the exact values they're about to save.
  const setConfig = (k: keyof typeof EMPTY_VSPHERE, v: string) => {
    setForm((f) => ({ ...f, config: { ...f.config, [k]: v } }));
    setTestResult(null);
  };

  // New vSphere pools must pass a connection test before they can be saved.
  // (On edit the password is blank = "keep current", so testing isn't possible
  // without re-entering it; re-test is optional there.)
  const requiresTest = form.type === "vsphere" && !editId;
  const saveBlocked = saving || !form.name || (requiresTest && !testResult?.ok);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">计算池管理</h1>
          <p className="text-sm text-gray-500 mt-1">配置 Docker 或 vSphere 资源池，用于制备 AI 实例</p>
        </div>
        <Button onClick={openCreate}><Plus className="h-4 w-4 mr-2" />添加计算池</Button>
      </div>

      {pools.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-gray-400">
            <Server className="h-10 w-10 mb-3" />
            <p>暂无计算池，点击右上角添加</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {pools.map((pool) => (
            <Card key={pool.id}>
              <CardContent className="p-4">
               <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`h-10 w-10 rounded-full flex items-center justify-center shrink-0 ${pool.type === "vsphere" ? "bg-blue-100" : "bg-gray-100"}`}>
                    <Server className={`h-5 w-5 ${pool.type === "vsphere" ? "text-blue-600" : "text-gray-600"}`} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-gray-900">{pool.name}</p>
                      <Badge variant={pool.type === "vsphere" ? "default" : "secondary"}>
                        {pool.type === "vsphere" ? "vSphere" : "Docker"}
                      </Badge>
                      <Badge variant={pool.enabled ? "success" : "secondary"}>
                        {pool.enabled ? "启用" : "禁用"}
                      </Badge>
                    </div>
                    {pool.type === "vsphere" && pool.config.host && (
                      <p className="text-sm text-gray-500 mt-0.5">
                        {pool.config.username}@{pool.config.host}
                        {pool.config.datacenter ? ` · ${pool.config.datacenter}` : ""}
                      </p>
                    )}
                    <p className="text-xs text-gray-400">{new Date(pool.createdAt).toLocaleDateString()}</p>
                  </div>
                </div>
                <div className="flex gap-2">
                  {pool.type === "vsphere" && (
                    <Button size="sm" variant="outline" onClick={() => handleInventory(pool.id)} disabled={invLoading && invPoolId === pool.id}>
                      {invLoading && invPoolId === pool.id ? "读取中..." : "浏览资源"}
                    </Button>
                  )}
                  <Button size="sm" variant="outline" onClick={() => handleToggle(pool)}>
                    {pool.enabled ? <X className="h-4 w-4" /> : <Check className="h-4 w-4" />}
                    {pool.enabled ? "禁用" : "启用"}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => openEdit(pool)}>
                    <Edit2 className="h-4 w-4" />
                  </Button>
                  <Button size="sm" variant="destructive" onClick={() => handleDelete(pool.id)} disabled={deleting === pool.id}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
               </div>

               {invPoolId === pool.id && invData && (
                <div className="mt-3 border-t pt-3 text-xs">
                  {invData.ok && invData.inventory ? (
                    <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-2">
                      {([
                        ["模板", "templates"],
                        ["数据存储", "datastores"],
                        ["网络端口组", "networks"],
                        ["资源池", "resourcePools"],
                        ["VM 文件夹", "folders"],
                      ] as const).map(([label, key]) => (
                        <div key={key}>
                          <p className="font-semibold text-gray-600">{label}（{invData.inventory?.[key]?.length ?? 0}）</p>
                          <ul className="text-gray-500 max-h-24 overflow-y-auto">
                            {(invData.inventory?.[key] ?? []).map((v) => (
                              <li key={v} className="truncate" title={v}>{v}</li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-red-600">{invData.error}</p>
                  )}
                </div>
               )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editId ? "编辑计算池" : "添加计算池"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium text-gray-700">名称</label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="生产-vCenter-01" />
            </div>
            {!editId && (
              <div>
                <label className="text-sm font-medium text-gray-700">类型</label>
                <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v as "vsphere" })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="vsphere">vSphere（虚拟机）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {form.type === "vsphere" && (
              <div className="space-y-2 border rounded-md p-3 bg-gray-50">
                <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">vCenter 连接</p>
                <p className="text-xs text-gray-400">只填连接信息。模板 / 数据存储 / 网络 / 资源池 / 文件夹在创建实例时从该 vCenter 实时选择。</p>
                {([
                  ["host", "vCenter 主机地址", "192.168.1.10"],
                  ["username", "用户名", "administrator@vsphere.local"],
                  ["password", "密码", editId ? "留空表示保留当前密码" : ""],
                  ["datacenter", "数据中心（可选，多数据中心时填）", "Datacenter"],
                ] as [keyof typeof EMPTY_VSPHERE, string, string][]).map(([key, label, ph]) => (
                  <div key={key}>
                    <label className="text-xs text-gray-600">{label}</label>
                    <Input
                      type={key === "password" ? "password" : "text"}
                      value={cfg[key]}
                      onChange={(e) => setConfig(key, e.target.value)}
                      placeholder={ph}
                      className="h-8 text-sm"
                    />
                  </div>
                ))}
                <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer pt-1">
                  <input
                    type="checkbox"
                    checked={skipTlsVerify}
                    onChange={(e) => { setSkipTlsVerify(e.target.checked); setTestResult(null); }}
                    className="h-3.5 w-3.5"
                  />
                  跳过证书校验（自签名证书 / 实验环境）
                </label>
                <div className="flex items-center gap-2 pt-1">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleTest}
                    disabled={testing || !form.config.host || !form.config.username || !form.config.password}
                  >
                    {testing ? "测试中..." : "测试 VC"}
                  </Button>
                  {testResult && (
                    <span className={`text-xs ${testResult.ok ? "text-green-600" : "text-red-600"}`}>
                      {testResult.ok ? "✓ 连接成功" : testResult.error}
                    </span>
                  )}
                </div>
              </div>
            )}

            {error && <p className="text-sm text-red-600">{error}</p>}
            {requiresTest && !testResult?.ok && (
              <p className="text-xs text-amber-600">请先「测试 VC」连接成功后才能保存。</p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
              <Button onClick={handleSave} disabled={saveBlocked}>
                {saving ? "保存中..." : "保存"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {pools.length < total && (
        <div className="flex justify-center py-2">
          <Button variant="outline" onClick={loadMore}>
            加载更多（已显示 {pools.length}/{total}）
          </Button>
        </div>
      )}
    </div>
  );
}
