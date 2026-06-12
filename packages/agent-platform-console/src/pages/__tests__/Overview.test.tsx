import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Overview from "../Overview";

// echarts pulls in canvas — jsdom can't render it. Replace with marker div
// that captures the option payload so we can assert node count + categories.
vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => (
    <div data-testid="echart" data-option={JSON.stringify(option)} />
  ),
}));

const healthzSpy = vi.fn();
const versionSpy = vi.fn();
const getVmsTopologySpy = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      healthz: (...args: unknown[]) => healthzSpy(...args),
      version: (...args: unknown[]) => versionSpy(...args),
      getVmsTopology: (...args: unknown[]) => getVmsTopologySpy(...args),
    },
  };
});

beforeEach(() => {
  healthzSpy.mockReset().mockResolvedValue({ status: "ok" });
  versionSpy.mockReset().mockResolvedValue({ version: "test-1.0" });
  getVmsTopologySpy.mockReset();
});

describe("Overview (W-2 topology wire)", () => {
  it("renders the Topology card and feeds echarts a 1-vCenter + 2-VM graph", async () => {
    getVmsTopologySpy.mockResolvedValue({
      nodes: [
        { id: "vcenter:default", name: "vcsa-01", category: "vcenter", state: "online", tenant: null },
        { id: "vm:vm-001", name: "alice-01", category: "vm", state: "running", tenant: "t-a" },
        { id: "vm:vm-002", name: "alice-02", category: "vm", state: "provisioning", tenant: "t-a" },
      ],
      edges: [
        { source: "vcenter:default", target: "vm:vm-001" },
        { source: "vcenter:default", target: "vm:vm-002" },
      ],
    });

    render(<Overview />);

    expect(screen.getByText("Topology")).toBeInTheDocument();

    await waitFor(() => expect(getVmsTopologySpy).toHaveBeenCalled());
    const chart = await screen.findByTestId("echart");
    const option = JSON.parse(chart.getAttribute("data-option") ?? "{}");
    expect(option.series[0].type).toBe("graph");
    expect(option.series[0].data).toHaveLength(3);
    expect(option.series[0].edges).toHaveLength(2);
  });

  it("shows the empty-state message when the API returns 0 nodes", async () => {
    getVmsTopologySpy.mockResolvedValue({ nodes: [], edges: [] });
    render(<Overview />);
    await waitFor(() => expect(getVmsTopologySpy).toHaveBeenCalled());
    expect(await screen.findByText(/No topology data yet/i)).toBeInTheDocument();
  });
});
