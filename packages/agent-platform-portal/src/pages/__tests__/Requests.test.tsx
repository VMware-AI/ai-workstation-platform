import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Requests from "../Requests";
import type { MyApproval } from "@/lib/api";

const listMyApprovals = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { listMyApprovals: (u: string) => listMyApprovals(u) },
  ApiError: class extends Error {},
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <Requests />
    </MemoryRouter>,
  );
}

const row = (overrides: Partial<MyApproval> = {}): MyApproval => ({
  id: 1,
  requester: "alice",
  package: "agent-vm-small",
  justification: "demo",
  state: "pending",
  created_at: "2026-05-30T08:00:00Z",
  decided_at: null,
  decided_by: null,
  decision_reason: null,
  ...overrides,
});

beforeEach(() => {
  listMyApprovals.mockReset();
  window.sessionStorage.setItem("agent-platform:user", "alice");
});

describe("Requests page — W-4", () => {
  it("shows empty state when no approvals", async () => {
    listMyApprovals.mockResolvedValue([]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/No requests yet/i)).toBeInTheDocument(),
    );
    expect(listMyApprovals).toHaveBeenCalledWith("alice");
  });

  it("renders three rows with state-specific badges", async () => {
    listMyApprovals.mockResolvedValue([
      row({ id: 1, state: "pending" }),
      row({ id: 2, state: "approved", decided_at: "2026-05-30T09:00:00Z" }),
      row({ id: 3, state: "rejected", decided_at: "2026-05-30T09:30:00Z", decision_reason: "out of quota" }),
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByText("#1")).toBeInTheDocument());
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("#3")).toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.getByText(/out of quota/)).toBeInTheDocument();
  });

  it("renders a timeline per approval row", async () => {
    listMyApprovals.mockResolvedValue([row({ id: 1 }), row({ id: 2, state: "approved" })]);
    renderPage();
    await waitFor(() => expect(screen.getByText("#1")).toBeInTheDocument());
    const timelines = screen.getAllByTestId("request-timeline");
    expect(timelines.length).toBe(2);
  });

  it("shows the API error message and hint when the endpoint fails", async () => {
    const ApiErrorClass = (await import("@/lib/api")).ApiError;
    listMyApprovals.mockRejectedValue(new ApiErrorClass(500, "boom"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/check that C1 has the C13 approval router/i)).toBeInTheDocument(),
    );
  });
});
