import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TokenUsage from "../TokenUsage";
import { mockInvocations, mockSummary, type UsageWindow } from "@/lib/api/tokenUsage";

// echarts-for-react drags in canvas — jsdom can't render it, and we don't
// want to assert on chart pixels anyway. Replace with a marker div.
vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => (
    <div data-testid="echart" data-option={JSON.stringify(option).slice(0, 80)} />
  ),
}));

const summarySpy = vi.fn();
const invocationsSpy = vi.fn();

vi.mock("@/lib/api/tokenUsage", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/tokenUsage")>(
    "@/lib/api/tokenUsage",
  );
  return {
    ...actual,
    tokenUsageApi: {
      summary: (...args: unknown[]) => summarySpy(...args),
      invocations: (...args: unknown[]) => invocationsSpy(...args),
    },
  };
});

beforeEach(() => {
  summarySpy.mockReset();
  invocationsSpy.mockReset();
});

describe("TokenUsage", () => {
  it("shows loading then renders KPI values from summary", async () => {
    const summary = { ...mockSummary("24h" as UsageWindow), total_cost_usd: 12.34 };
    summarySpy.mockResolvedValue(summary);
    invocationsSpy.mockResolvedValue(mockInvocations(20));

    render(<TokenUsage />);

    // Heading is always present.
    expect(screen.getByRole("heading", { name: "Token Usage" })).toBeInTheDocument();

    // KPI labels render unconditionally.
    expect(screen.getByText("Total tokens")).toBeInTheDocument();
    expect(screen.getByText("Total cost")).toBeInTheDocument();
    expect(screen.getByText("Active users")).toBeInTheDocument();
    expect(screen.getByText("Active agents")).toBeInTheDocument();

    // Values land after the summary resolves.
    await waitFor(() => {
      expect(screen.getByText("$12.34")).toBeInTheDocument();
    });
    expect(screen.getByText(String(summary.active_users))).toBeInTheDocument();
    expect(screen.getByText(String(summary.active_agents))).toBeInTheDocument();

    // All three charts are mounted.
    expect(screen.getAllByTestId("echart")).toHaveLength(3);

    // Recent invocations table renders rows (mock fixture uses 'alice' as first user).
    expect(screen.getByText("Recent invocations")).toBeInTheDocument();
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
  });

  it("flags demo data when the API returns _mock: true", async () => {
    summarySpy.mockResolvedValue({ ...mockSummary("24h"), _mock: true });
    invocationsSpy.mockResolvedValue({ ...mockInvocations(20), _mock: true });

    render(<TokenUsage />);

    await waitFor(() => {
      expect(screen.getByText("demo data")).toBeInTheDocument();
    });
  });

  it("re-fetches summary when the user switches the time window", async () => {
    summarySpy.mockResolvedValue(mockSummary("24h"));
    invocationsSpy.mockResolvedValue(mockInvocations(20));

    render(<TokenUsage />);

    await waitFor(() => expect(summarySpy).toHaveBeenCalledWith("24h"));

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Last 7d" }));

    await waitFor(() => expect(summarySpy).toHaveBeenCalledWith("7d"));
  });

  it("surfaces an error message when both calls reject", async () => {
    summarySpy.mockRejectedValue(new Error("boom"));
    invocationsSpy.mockRejectedValue(new Error("boom"));

    render(<TokenUsage />);

    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });
});
