import { describe, it, expect } from "vitest";
import { buildTimeSeriesLineOption } from "../lineOption";

// The wrapper that renders the option needs a DOM (echarts) so it is not unit
// tested here; the *option shape* is pure and fully testable in the node env.

const base = {
  categories: ["10:00", "10:01"],
  name: "CPU",
  values: [12, 34],
  color: "#3b82f6",
};

describe("buildTimeSeriesLineOption", () => {
  it("maps categories to the x axis and values to a single line series", () => {
    const o = buildTimeSeriesLineOption(base);
    expect((o.xAxis as { data: string[] }).data).toEqual(["10:00", "10:01"]);
    const series = o.series as Array<Record<string, unknown>>;
    expect(series).toHaveLength(1);
    expect(series[0].type).toBe("line");
    expect(series[0].name).toBe("CPU");
    expect(series[0].data).toEqual([12, 34]);
    expect((series[0].lineStyle as { color: string }).color).toBe("#3b82f6");
    expect((series[0].itemStyle as { color: string }).color).toBe("#3b82f6");
  });

  it("appends the unit to y-axis labels", () => {
    const o = buildTimeSeriesLineOption({ ...base, unit: "%" });
    const formatter = (o.yAxis as { axisLabel: { formatter: (v: number) => string } }).axisLabel
      .formatter;
    expect(formatter(50)).toBe("50%");
  });

  it("defaults the unit to an empty string", () => {
    const o = buildTimeSeriesLineOption(base);
    const formatter = (o.yAxis as { axisLabel: { formatter: (v: number) => string } }).axisLabel
      .formatter;
    expect(formatter(50)).toBe("50");
  });

  it("hides point symbols and uses a 2px stroke (matches the prior recharts look)", () => {
    const o = buildTimeSeriesLineOption(base);
    const series = o.series as Array<Record<string, unknown>>;
    expect(series[0].showSymbol).toBe(false);
    expect((series[0].lineStyle as { width: number }).width).toBe(2);
  });
});
