import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MyAgents from "../MyAgents";

const listMyAgents = vi.fn();
const myUsage = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listMyAgents: () => listMyAgents(),
    myUsage: (d: number) => myUsage(d),
  },
  ApiError: class extends Error {},
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <MyAgents />
    </MemoryRouter>,
  );
}

const baseAgent = {
  id: "vm-1",
  name: "alice-running",
  template: "v0.1.0",
  state: "running" as const,
  createdAt: "2026-05-30T08:00:00Z",
};

beforeEach(() => {
  listMyAgents.mockReset();
  myUsage.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MyAgents — W-5 fileshare path + quota", () => {
  it("renders the fileshare workspace path when C1 returns one", async () => {
    listMyAgents.mockResolvedValue({
      agents: [{ ...baseAgent, fileshare_path: "\\\\fs.example.local\\u\\alice\\workspace" }],
    });
    myUsage.mockResolvedValue({ days: [], total_tokens: 0, quota_total: 0, quota_used: 0 });

    renderPage();
    await waitFor(() => expect(screen.getByText("alice-running")).toBeInTheDocument());
    expect(
      screen.getByText("\\\\fs.example.local\\u\\alice\\workspace"),
    ).toBeInTheDocument();
  });

  it("omits the fileshare row when path is null (fileshare_base unset)", async () => {
    listMyAgents.mockResolvedValue({
      agents: [{ ...baseAgent, fileshare_path: null }],
    });
    myUsage.mockResolvedValue({ days: [], total_tokens: 0, quota_total: 0, quota_used: 0 });

    renderPage();
    await waitFor(() => expect(screen.getByText("alice-running")).toBeInTheDocument());
    expect(screen.queryByText(/workspace:/)).not.toBeInTheDocument();
  });

  it("renders mac copy button label as smb:// when navigator.platform is MacIntel", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      platform: "MacIntel",
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    listMyAgents.mockResolvedValue({
      agents: [{ ...baseAgent, fileshare_path: "\\\\fs.example.local\\u\\alice\\workspace" }],
    });
    myUsage.mockResolvedValue({ days: [], total_tokens: 0, quota_total: 0, quota_used: 0 });

    renderPage();
    const btn = await screen.findByRole("button", { name: /copy smb:\/\//i });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        "smb://fs.example.local/u/alice/workspace",
      ),
    );
  });

  it("renders the quota progress bar with used/total formatting", async () => {
    listMyAgents.mockResolvedValue({ agents: [] });
    myUsage.mockResolvedValue({
      days: [],
      total_tokens: 600_000,
      quota_total: 1_000_000,
      quota_used: 600_000,
    });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Token quota/)).toBeInTheDocument());
    expect(screen.getByText(/600,000 \/ 1,000,000 \(60%\)/)).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("600000");
    expect(bar.getAttribute("aria-valuemax")).toBe("1000000");
  });

  it("hides the quota card when quota_total is absent (legacy backend)", async () => {
    listMyAgents.mockResolvedValue({ agents: [] });
    myUsage.mockResolvedValue({ days: [], total_tokens: 0 });

    renderPage();
    await waitFor(() => expect(screen.getByText("No agents yet.", { exact: false })).toBeInTheDocument());
    expect(screen.queryByText(/Token quota/)).not.toBeInTheDocument();
  });
});
