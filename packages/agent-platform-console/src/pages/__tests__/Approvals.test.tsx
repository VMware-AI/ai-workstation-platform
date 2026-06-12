import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import Approvals from "../Approvals";

const listApprovals = vi.fn();
const getApproval = vi.fn();
const approveRequest = vi.fn();
const rejectRequest = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listApprovals: (...args: unknown[]) => listApprovals(...args),
      getApproval: (...args: unknown[]) => getApproval(...args),
      approveRequest: (...args: unknown[]) => approveRequest(...args),
      rejectRequest: (...args: unknown[]) => rejectRequest(...args),
    },
  };
});

describe("Approvals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the heading and filter buttons", async () => {
    listApprovals.mockResolvedValue([]);
    render(<Approvals />);
    expect(screen.getByText("Approvals")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pending" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approved" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rejected" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
  });

  it("defaults to pending filter on first load", async () => {
    listApprovals.mockResolvedValue([]);
    render(<Approvals />);
    await waitFor(() => expect(listApprovals).toHaveBeenCalled());
    expect(listApprovals).toHaveBeenCalledWith({ state: "pending", limit: 100 });
  });

  it("shows empty-state copy when no rows come back", async () => {
    listApprovals.mockResolvedValue([]);
    render(<Approvals />);
    expect(await screen.findByText("No requests in this view.")).toBeInTheDocument();
  });

  it("renders one row per returned request with state badge", async () => {
    listApprovals.mockResolvedValue([
      {
        id: 1,
        requester: "alice",
        package: "agent-vm-small",
        justification: "M0 eval",
        state: "pending",
        created_at: "2026-05-23T01:23:45Z",
        decided_at: null,
        decided_by: null,
        decision_reason: null,
        audit_events: [],
      },
    ]);
    render(<Approvals />);
    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText("agent-vm-small")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("surfaces transport error with a hint pointing at the C13 mount path", async () => {
    listApprovals.mockRejectedValue(new Error("ECONNREFUSED"));
    render(<Approvals />);
    expect(await screen.findByText(/ECONNREFUSED/)).toBeInTheDocument();
    expect(
      screen.getByText(/router may not be mounted yet/i),
    ).toBeInTheDocument();
  });

  it("ignores a stale response when the filter changes mid-flight", async () => {
    const row = (id: number, requester: string, state: string) => ({
      id,
      requester,
      package: `pkg-${id}`,
      justification: "x",
      state,
      created_at: "2026-05-23T01:23:45Z",
      decided_at: null,
      decided_by: null,
      decision_reason: null,
      audit_events: [],
    });

    // First request (pending filter) hangs until we resolve it manually;
    // second request (approved filter) resolves immediately.
    let resolveStale!: (rows: unknown) => void;
    const stale = new Promise((res) => {
      resolveStale = res;
    });
    listApprovals.mockImplementation((args: { state?: string }) =>
      args.state === "pending" ? stale : Promise.resolve([row(2, "bob", "approved")]),
    );

    render(<Approvals />);
    await waitFor(() => expect(listApprovals).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Approved" }));
    expect(await screen.findByText("bob")).toBeInTheDocument();

    // The stale pending response lands late — guarded effect must drop it.
    await act(async () => {
      resolveStale([row(1, "alice", "pending")]);
    });

    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.queryByText("alice")).toBeNull();
  });
});
