"use client";
import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CreditCard } from "lucide-react";
import { Button } from "@/components/ui/button";

interface UsageRecord {
  id: string;
  timestamp: string;
  totalTokens: number;
  costCents: number;
  model: string;
  instance: { name: string };
}

export default function BillingPage() {
  const [records, setRecords] = useState<UsageRecord[]>([]);
  const [totals, setTotals] = useState({ totalTokens: 0, totalCostCents: 0, count: 0 });
  const [total, setTotal] = useState(0);

  useEffect(() => {
    fetch("/api/billing/usage?days=30")
      .then(r => r.json())
      .then(d => { setRecords(d.records || []); setTotals(d.totals || {}); setTotal(d.total ?? 0); });
  }, []);

  async function loadMore() {
    const res = await fetch(`/api/billing/usage?days=30&skip=${records.length}`);
    if (res.ok) {
      const d = await res.json();
      setRecords((prev) => [...prev, ...(d.records ?? [])]);
      setTotal(d.total ?? 0);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">计费用量</h1>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <Card>
          <CardHeader><CardTitle className="text-sm text-gray-500">本月 Token 用量</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{(totals.totalTokens / 1000).toFixed(1)}K</div></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-gray-500">本月费用（估算）</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold text-orange-600">${(totals.totalCostCents / 100).toFixed(4)}</div></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-gray-500">调用次数</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{totals.count}</div></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>调用记录（最近30天）</CardTitle></CardHeader>
        <CardContent>
          {records.length === 0 ? (
            <div className="flex flex-col items-center py-8 text-gray-400">
              <CreditCard className="h-8 w-8 mb-2" />
              <p>暂无记录</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-gray-500">
                    <th className="text-left py-2 pr-4">时间</th>
                    <th className="text-left py-2 pr-4">实例</th>
                    <th className="text-left py-2 pr-4">模型</th>
                    <th className="text-right py-2 pr-4">Token</th>
                    <th className="text-right py-2">费用</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map(r => (
                    <tr key={r.id} className="border-b hover:bg-gray-50">
                      <td className="py-2 pr-4 text-gray-500">{new Date(r.timestamp).toLocaleString()}</td>
                      <td className="py-2 pr-4">{r.instance?.name || "-"}</td>
                      <td className="py-2 pr-4"><Badge variant="outline">{r.model}</Badge></td>
                      <td className="py-2 pr-4 text-right">{r.totalTokens.toLocaleString()}</td>
                      <td className="py-2 text-right text-orange-600">${(r.costCents / 100).toFixed(6)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
      {records.length < total && (
        <div className="flex justify-center py-2">
          <Button variant="outline" onClick={loadMore}>
            加载更多（已显示 {records.length}/{total}）
          </Button>
        </div>
      )}
    </div>
  );
}
