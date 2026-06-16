import type { EChartsOption } from "echarts";

export interface TimeSeriesLineSpec {
  /** X-axis category labels (e.g. formatted timestamps). */
  categories: string[];
  /** Series name shown in tooltips. */
  name: string;
  /** Y values, index-aligned with `categories`. */
  values: number[];
  /** Line + point colour (hex). */
  color: string;
  /** Appended to y-axis labels, e.g. "%" or " MB". Defaults to "". */
  unit?: string;
}

/**
 * Build the ECharts option for a single-series time-series line chart.
 *
 * Pure (no DOM) so it is unit-testable in the node test env; the {@link EChart}
 * wrapper renders whatever option this returns. Mirrors the look of the
 * recharts charts it replaces: faint grid, small ticks, smooth 2px line,
 * no point symbols, unit-suffixed y labels.
 */
export function buildTimeSeriesLineOption(spec: TimeSeriesLineSpec): EChartsOption {
  const { categories, name, values, color, unit = "" } = spec;
  return {
    grid: { top: 16, right: 16, bottom: 28, left: 52 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: categories,
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 11, formatter: (value: number) => `${value}${unit}` },
      splitLine: { lineStyle: { color: "#f0f0f0" } },
    },
    series: [
      {
        name,
        type: "line",
        smooth: true,
        showSymbol: false,
        data: values,
        lineStyle: { width: 2, color },
        itemStyle: { color },
      },
    ],
  };
}
