"use client";
import { useState, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { ArrowLeft, Server, RefreshCw, UploadCloud } from "lucide-react";
import { parseDeployCsv, CSV_COLUMNS } from "@/lib/providers/vsphere/cloudinit/csv";
import { getAgentRuncmd } from "@/lib/providers/vsphere/cloudinit/agents";

interface Pool { id: string; name: string; type: string; enabled: boolean }
interface Inventory {
  datastores: string[];
  networks: string[];
  resourcePools: string[];
  folders: string[];
  templates: string[];
}

const NONE = "__none__";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-sm font-medium text-gray-700">{label}</label>
      {children}
    </div>
  );
}

function PlacementSelect({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <Field label={label}>
      <Select value={value || NONE} onValueChange={(v) => onChange(v === NONE ? "" : v)}>
        <SelectTrigger><SelectValue placeholder={placeholder} /></SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE}>{placeholder}</SelectItem>
          {options.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
        </SelectContent>
      </Select>
    </Field>
  );
}

export default function DeployPage() {
  const router = useRouter();
  const [pools, setPools] = useState<Pool[]>([]);
  const [poolId, setPoolId] = useState("");
  const [inv, setInv] = useState<Inventory | null>(null);
  const [invLoading, setInvLoading] = useState(false);
  const [invError, setInvError] = useState("");

  // placement (clone-time)
  const [templateName, setTemplateName] = useState("");
  const [datastore, setDatastore] = useState("");
  const [network, setNetwork] = useState("");
  const [resourcePool, setResourcePool] = useState("");
  const [folder, setFolder] = useState("");

  // shared cloud-init
  const [agentType, setAgentType] = useState("");
  const [installCommands, setInstallCommands] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");

  // mode + single VM
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [vmName, setVmName] = useState("");
  const [netMode, setNetMode] = useState<"dhcp" | "static">("dhcp");
  const [ip, setIp] = useState("");
  const [prefix, setPrefix] = useState("24");
  const [gateway, setGateway] = useState("");
  const [dns, setDns] = useState("");
  const [osUser, setOsUser] = useState("");
  const [osPassword, setOsPassword] = useState("");
  const [sshKey, setSshKey] = useState("");

  // batch
  const [csv, setCsv] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [submitWarning, setSubmitWarning] = useState("");

  useEffect(() => {
    fetch("/api/compute-pools")
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items?: Pool[] } | Pool[]) => {
        const list = Array.isArray(d) ? d : (d.items ?? []);
        setPools(list.filter((p) => p.type === "vsphere" && p.enabled));
      })
      .catch(() => setPools([]));
  }, []);

  const loadInventory = useCallback(async () => {
    if (!poolId) return;
    setInvLoading(true);
    setInvError("");
    try {
      const res = await fetch(`/api/compute-pools/${poolId}/inventory`, { method: "POST" });
      const data = await res.json();
      if (data.ok) setInv(data.inventory);
      else setInvError(data.error || "加载资源失败");
    } catch (e) {
      setInvError(e instanceof Error ? e.message : "加载资源失败");
    } finally {
      setInvLoading(false);
    }
  }, [poolId]);

  const csvPreview = useMemo(
    () => (mode === "batch" && csv.trim() ? parseDeployCsv(csv) : null),
    [mode, csv],
  );

  function buildBody() {
    const list = (s: string, sep: RegExp) => s.split(sep).map((x) => x.trim()).filter(Boolean);
    const shared = {
      templateName,
      datastore: datastore || undefined,
      network: network || undefined,
      resourcePool: resourcePool || undefined,
      folder: folder || undefined,
      // The chosen agent's install commands are expanded into installCommands in
      // the UI (see the Agent 类型 select), so we send only installCommands —
      // also sending agentType would duplicate the registry commands server-side.
      installCommands: installCommands.trim() ? list(installCommands, /\n/) : undefined,
      llmBaseUrl: llmBaseUrl || undefined,
      llmApiKey: llmApiKey || undefined,
    };
    if (mode === "single") {
      const network_ =
        netMode === "dhcp"
          ? { mode: "dhcp" as const }
          : {
              mode: "static" as const,
              ip,
              prefix: Number(prefix),
              gateway,
              dns: list(dns, /[;\s]+/),
            };
      return {
        computePoolId: poolId,
        mode,
        shared,
        vm: { name: vmName, network: network_, osUser, osPassword, sshKey: sshKey || undefined },
      };
    }
    return { computePoolId: poolId, mode, shared, csv };
  }

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitError("");
    setSubmitWarning("");
    try {
      const res = await fetch("/api/instances/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBody()),
      });
      if (res.ok) {
        const ok = await res.json().catch(() => ({}));
        // Instances were created. If the API flagged that no worker is online
        // (#259), show it and let the user read it instead of silently landing
        // on a list of PENDING rows that will never move.
        if (ok.warning) {
          setSubmitWarning(ok.warning);
          return;
        }
        router.push("/instances");
        return;
      }
      const data = await res.json().catch(() => ({}));
      const details = Array.isArray(data.details)
        ? data.details.map((d: { line?: number; message: string }) => (d.line ? `行 ${d.line}: ` : "") + d.message).join("；")
        : "";
      setSubmitError(data.error ? `${data.error}${details ? " — " + details : ""}` : "部署失败");
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "部署失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function onCsvFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) setCsv(await file.text());
  }

  const submitDisabled =
    submitting ||
    // Instances were already created on the warning branch — block a second
    // click (which would create a duplicate batch); the user continues via the
    // "前往实例列表" link in the banner.
    !!submitWarning ||
    !poolId ||
    !templateName ||
    (mode === "single" ? !vmName || !osUser || !osPassword : !csv.trim() || (csvPreview?.errors.length ?? 0) > 0);

  return (
    <div className="max-w-3xl">
      <Link href="/instances" className="inline-flex items-center gap-1 text-sm text-gray-500 mb-4 hover:text-gray-700">
        <ArrowLeft className="h-4 w-4" /> 返回实例
      </Link>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">从模板创建（vSphere）</h1>

      <Card className="mb-4">
        <CardContent className="p-4 space-y-3">
          <h2 className="font-medium text-gray-800 flex items-center gap-2"><Server className="h-4 w-4" /> 计算池与资源</h2>
          <Field label="计算池（vSphere）">
            <Select value={poolId} onValueChange={(v) => { setPoolId(v); setInv(null); }}>
              <SelectTrigger><SelectValue placeholder="选择 vSphere 计算池" /></SelectTrigger>
              <SelectContent>
                {pools.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Button variant="outline" size="sm" onClick={loadInventory} disabled={!poolId || invLoading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${invLoading ? "animate-spin" : ""}`} /> 加载资源
          </Button>
          {invError && <p className="text-sm text-red-600">{invError}</p>}
          {inv && (
            <div className="grid grid-cols-2 gap-3">
              <PlacementSelect label="VM 模板 *" value={templateName} options={inv.templates} onChange={setTemplateName} placeholder="选择模板" />
              <PlacementSelect label="Datastore" value={datastore} options={inv.datastores} onChange={setDatastore} placeholder="（默认）" />
              <PlacementSelect label="网络端口组" value={network} options={inv.networks} onChange={setNetwork} placeholder="（默认）" />
              <PlacementSelect label="资源池" value={resourcePool} options={inv.resourcePools} onChange={setResourcePool} placeholder="（默认）" />
              <PlacementSelect label="文件夹" value={folder} options={inv.folders} onChange={setFolder} placeholder="（默认）" />
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="p-4 space-y-3">
          <div className="flex gap-2">
            <Button variant={mode === "single" ? "default" : "outline"} size="sm" onClick={() => setMode("single")}>单台</Button>
            <Button variant={mode === "batch" ? "default" : "outline"} size="sm" onClick={() => setMode("batch")}>CSV 批量</Button>
          </div>

          {mode === "single" ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="主机名 / 实例名 *">
                <Input value={vmName} onChange={(e) => setVmName(e.target.value)} placeholder="web-01" />
              </Field>
              <Field label="网络">
                <Select value={netMode} onValueChange={(v) => setNetMode(v as "dhcp" | "static")}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="dhcp">DHCP</SelectItem>
                    <SelectItem value="static">静态 IP</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {netMode === "static" && (
                <>
                  <Field label="IP"><Input value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.0.1.5" /></Field>
                  <Field label="前缀长度"><Input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="24" /></Field>
                  <Field label="网关"><Input value={gateway} onChange={(e) => setGateway(e.target.value)} placeholder="10.0.1.1" /></Field>
                  <Field label="DNS（空格/分号分隔）"><Input value={dns} onChange={(e) => setDns(e.target.value)} placeholder="8.8.8.8 1.1.1.1" /></Field>
                </>
              )}
              <Field label="系统用户 *"><Input value={osUser} onChange={(e) => setOsUser(e.target.value)} placeholder="ops" /></Field>
              <Field label="系统密码 *"><Input type="password" value={osPassword} onChange={(e) => setOsPassword(e.target.value)} /></Field>
              <div className="col-span-2">
                <Field label="SSH 公钥（可选）"><Input value={sshKey} onChange={(e) => setSshKey(e.target.value)} placeholder="ssh-ed25519 AAAA..." /></Field>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-500">列：{CSV_COLUMNS.join(", ")}（空 IP = DHCP）</p>
              <label className="inline-flex items-center gap-1 text-sm text-blue-600 cursor-pointer">
                <UploadCloud className="h-4 w-4" /> 上传 CSV
                <input type="file" accept=".csv,text/csv" className="hidden" onChange={onCsvFile} />
              </label>
              <Textarea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={`${CSV_COLUMNS.join(",")}\nweb-01,10.0.1.5,255.255.255.0,10.0.1.1,8.8.8.8,ops,pw,`} />
              {csvPreview && (
                <div className="text-sm">
                  <p className="text-gray-600">解析到 {csvPreview.rows.length} 台</p>
                  {csvPreview.errors.length > 0 && (
                    <ul className="text-red-600 list-disc ml-5">
                      {csvPreview.errors.map((er, i) => <li key={i}>{er.line ? `行 ${er.line}: ` : ""}{er.message}</li>)}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="p-4 grid grid-cols-2 gap-3">
          <h2 className="font-medium text-gray-800 col-span-2">共享配置</h2>
          <div className="col-span-2">
            <Field label="Agent 类型">
              <Select
                value={agentType || NONE}
                onValueChange={(v) => {
                  const type = v === NONE ? "" : v;
                  setAgentType(type);
                  // 选 agent 即把其安装命令填入下方 runcmd（可继续编辑）；选「不安装」清空。
                  setInstallCommands(type ? getAgentRuncmd(type).join("\n") : "");
                }}
              >
                <SelectTrigger><SelectValue placeholder="（不安装）" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>（不安装）</SelectItem>
                  <SelectItem value="xiaoguai">xiaoguai</SelectItem>
                  <SelectItem value="goose">goose</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
          <div className="col-span-2">
            <Field label="共享 runcmd（每行一条命令；选 Agent 类型会自动填入）">
              <Textarea rows={3} value={installCommands} onChange={(e) => setInstallCommands(e.target.value)} placeholder="systemctl restart myagent" />
            </Field>
          </div>
          <Field label="LLM Base URL（递给 agent）"><Input value={llmBaseUrl} onChange={(e) => setLlmBaseUrl(e.target.value)} placeholder="https://gateway/v1" /></Field>
          <Field label="LLM API Key（加密存储）"><Input type="password" value={llmApiKey} onChange={(e) => setLlmApiKey(e.target.value)} /></Field>
        </CardContent>
      </Card>

      {submitError && <p className="text-sm text-red-600 mb-3">{submitError}</p>}
      {submitWarning && (
        <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-medium">实例已创建，但有提醒：</p>
          <p className="mt-1">{submitWarning}</p>
          <Link href="/instances" className="mt-2 inline-block underline">前往实例列表 →</Link>
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Link href="/instances"><Button variant="outline">取消</Button></Link>
        <Button onClick={handleSubmit} disabled={submitDisabled}>{submitting ? "部署中…" : "部署"}</Button>
      </div>
    </div>
  );
}
