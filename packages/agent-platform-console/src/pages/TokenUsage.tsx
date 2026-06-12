import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import {
  tokenUsageApi,
  type InvocationListResponse,
  type UsageBucket,
  type UsageSummary,
  type UsageWindow,
} from "@/lib/api/tokenUsage";

const WINDOWS: { value: UsageWindow; label: string }[] = [
  { value: "24h", label: "Last 24h" },
  { value: "7d", label: "Last 7d" },
  { value: "30d", label: "Last 30d" },
];

type PageState = {
  summary?: UsageSummary;
  invocations?: InvocationListResponse;
  loading: boolean;
  error?: string;
};

export default function TokenUsage() {
  const [window, setWindow] = useState<UsageWindow>("24h");
  const [state, setState] = useState<PageState>({ loading: true });

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    (async () => {
      try {
        const [summary, invocations] = await Promise.all([
          tokenUsageApi.summary(window),
          tokenUsageApi.invocations(20),
        ]);
        if (!cancelled) {
          setState({ summary, invocations, loading: false });
        }
      } catch (e) {
        if (!cancelled) setState({ loading: false, error: String(e) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [window]);

  const isMock = Boolean(state.summary?._mock || state.invocations?._mock);

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Token Usage</h1>
          <p className="text-sm text-muted-foreground">
            LLM consumption rolled up by agent, user, and model. Data flows
            C5 gateway → C7 telemetry shim → C1 ingest.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isMock && <Badge variant="warning">demo data</Badge>}
          <WindowPicker value={window} onChange={setWindow} />
        </div>
      </header>

      {state.error && (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">{state.error}</CardContent>
        </Card>
      )}

      <KpiGrid summary={state.summary} loading={state.loading} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Tokens by agent">
          {state.summary ? (
            <ReactECharts
              option={byAgentOption(state.summary.by_agent)}
              style={{ height: 320 }}
              notMerge
            />
          ) : (
            <ChartPlaceholder />
          )}
        </ChartCard>
        <ChartCard title="Top users by tokens">
          {state.summary ? (
            <ReactECharts
              option={byUserOption(state.summary.by_user)}
              style={{ height: 320 }}
              notMerge
            />
          ) : (
            <ChartPlaceholder />
          )}
        </ChartCard>
        <ChartCard title="Share by model" className="lg:col-span-2">
          {state.summary ? (
            <ReactECharts
              option={byModelOption(state.summary.by_model)}
              style={{ height: 320 }}
              notMerge
            />
          ) : (
            <ChartPlaceholder />
          )}
        </ChartCard>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent invocations</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <InvocationTable data={state.invocations} loading={state.loading} />
        </CardContent>
      </Card>
    </div>
  );
}

// ----- subcomponents ---------------------------------------------------------

function WindowPicker({
  value,
  onChange,
}: {
  value: UsageWindow;
  onChange: (w: UsageWindow) => void;
}) {
  return (
    <div className="inline-flex gap-1 rounded-md border p-0.5" role="tablist">
      {WINDOWS.map((w) => (
        <Button
          key={w.value}
          size="sm"
          variant={value === w.value ? "default" : "ghost"}
          onClick={() => onChange(w.value)}
          role="tab"
          aria-selected={value === w.value}
        >
          {w.label}
        </Button>
      ))}
    </div>
  );
}

function KpiGrid({ summary, loading }: { summary?: UsageSummary; loading: boolean }) {
  const cards = useMemo(
    () => [
      {
        title: "Total tokens",
        value: summary ? formatCompact(summary.total_tokens) : "—",
      },
      {
        title: "Total cost",
        value: summary ? `$${summary.total_cost_usd.toFixed(2)}` : "—",
      },
      {
        title: "Active users",
        value: summary ? String(summary.active_users) : "—",
      },
      {
        title: "Active agents",
        value: summary ? String(summary.active_agents) : "—",
      },
    ],
    [summary],
  );

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      {cards.map((c) => (
        <Card key={c.title}>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {c.title}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {loading && !summary ? "…" : c.value}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ChartCard({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function ChartPlaceholder() {
  return (
    <div className="flex h-[320px] items-center justify-center text-sm text-muted-foreground">
      Loading chart…
    </div>
  );
}

function InvocationTable({
  data,
  loading,
}: {
  data?: InvocationListResponse;
  loading: boolean;
}) {
  if (loading && !data) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (!data) return null;
  return (
    <Table>
      <THead>
        <TR>
          <TH>Time</TH>
          <TH>User</TH>
          <TH>Agent</TH>
          <TH>Model</TH>
          <TH className="text-right">Prompt</TH>
          <TH className="text-right">Completion</TH>
          <TH className="text-right">Cost (USD)</TH>
        </TR>
      </THead>
      <TBody>
        {data.invocations.length === 0 ? (
          <TR>
            <TD colSpan={7} className="py-6 text-center text-muted-foreground">
              No invocations recorded in this window.
            </TD>
          </TR>
        ) : (
          data.invocations.map((row) => (
            <TR key={row.id}>
              <TD className="font-mono text-xs">{row.at}</TD>
              <TD>{row.user}</TD>
              <TD>{row.agent}</TD>
              <TD className="font-mono text-xs">{row.model}</TD>
              <TD className="text-right tabular-nums">{row.prompt_tokens.toLocaleString()}</TD>
              <TD className="text-right tabular-nums">
                {row.completion_tokens.toLocaleString()}
              </TD>
              <TD className="text-right tabular-nums">${row.cost_usd.toFixed(4)}</TD>
            </TR>
          ))
        )}
      </TBody>
    </Table>
  );
}

// ----- echarts option builders ----------------------------------------------

function byAgentOption(buckets: UsageBucket[]): EChartsOption {
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 40, right: 16, top: 24, bottom: 32 },
    xAxis: { type: "category", data: buckets.map((b) => b.key) },
    yAxis: { type: "value", name: "tokens" },
    series: [
      {
        name: "tokens",
        type: "bar",
        stack: "total",
        data: buckets.map((b) => b.tokens),
        itemStyle: { color: "#6366f1" },
      },
    ],
  };
}

function byUserOption(buckets: UsageBucket[]): EChartsOption {
  // Top 10, sorted desc; horizontal bars.
  const sorted = [...buckets].sort((a, b) => b.tokens - a.tokens).slice(0, 10);
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 80, right: 24, top: 24, bottom: 32 },
    xAxis: { type: "value", name: "tokens" },
    yAxis: {
      type: "category",
      data: sorted.map((b) => b.key).reverse(),
    },
    series: [
      {
        name: "tokens",
        type: "bar",
        data: sorted.map((b) => b.tokens).reverse(),
        itemStyle: { color: "#10b981" },
      },
    ],
  };
}

function byModelOption(buckets: UsageBucket[]): EChartsOption {
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { bottom: 0 },
    series: [
      {
        name: "tokens",
        type: "pie",
        radius: ["40%", "70%"],
        avoidLabelOverlap: true,
        label: { show: true, formatter: "{b}\n{d}%" },
        data: buckets.map((b) => ({ name: b.key, value: b.tokens })),
      },
    ],
  };
}

function formatCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
