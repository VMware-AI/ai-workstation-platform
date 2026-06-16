"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

/**
 * Thin client-side wrapper around an ECharts instance.
 *
 * echarts only touches the DOM inside the effect (never at module load), so a
 * plain "use client" component is safe to SSR-prerender — the div renders empty
 * on the server and the chart initialises on the client. Reused by every chart
 * in the app (monitoring, topology, …) so we depend on `echarts` directly and
 * skip `echarts-for-react` (one less React-version-coupled dependency).
 */
export function EChart({
  option,
  height = 200,
  className,
}: {
  option: EChartsOption;
  height?: number | string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);

  // Init once on mount; keep the instance responsive and dispose on unmount.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(el);
    return () => {
      resize.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // Re-apply on option change. notMerge=true so dropping series/data does not
  // leave stale state behind (e.g. switching the selected instance).
  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={containerRef} className={className} style={{ height, width: "100%" }} />;
}
