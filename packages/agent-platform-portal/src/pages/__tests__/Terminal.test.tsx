import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AttachAddon } from "@xterm/addon-attach";
import Terminal from "../Terminal";

// xterm.js touches canvas + sizing APIs that jsdom doesn't implement —
// stub the whole package so the page logic (state machine, fallback,
// reconnect) is what we actually test.
const mockOpen = vi.fn();
const mockWrite = vi.fn();
const mockWriteln = vi.fn();
const mockDispose = vi.fn();
const mockLoadAddon = vi.fn();
const mockOnData = vi.fn();

vi.mock("@xterm/xterm", () => ({
  Terminal: vi.fn(() => ({
    open: mockOpen,
    write: mockWrite,
    writeln: mockWriteln,
    dispose: mockDispose,
    loadAddon: mockLoadAddon,
    onData: mockOnData,
  })),
}));
vi.mock("@xterm/addon-attach", () => ({ AttachAddon: vi.fn() }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: vi.fn(() => ({ fit: vi.fn() })) }));
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

const mockGetTtydUrl = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { getTtydUrl: (...args: unknown[]) => mockGetTtydUrl(...args) },
  ApiError: class extends Error {},
}));

// WebSocket constructor isn't in jsdom — stub it so a successful
// getTtydUrl path doesn't blow up the test.
const wsInstances: FakeWebSocket[] = [];
class FakeWebSocket {
  binaryType = "";
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close() {}
  constructor(
    public url: string,
    public protocols?: string | string[],
  ) {
    wsInstances.push(this);
  }
}
vi.stubGlobal("WebSocket", FakeWebSocket);

function renderAt(path = "/terminal/vm-123") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/terminal/:vmId" element={<Terminal />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Terminal page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsInstances.length = 0;
  });

  it("renders heading + vmId", async () => {
    mockGetTtydUrl.mockResolvedValueOnce({ url: "ws://example/ttyd" });
    renderAt("/terminal/vm-abc");
    expect(screen.getByText("Terminal")).toBeInTheDocument();
    expect(screen.getByText("vm-abc")).toBeInTheDocument();
    await waitFor(() => expect(mockGetTtydUrl).toHaveBeenCalledWith("vm-abc"));
  });

  it("falls back to Demo mode when the C1 endpoint fails", async () => {
    mockGetTtydUrl.mockRejectedValueOnce(new Error("404 not found"));
    renderAt();
    await screen.findByText("Demo mode");
    // demo mode prints a banner line and a prompt into xterm
    expect(mockWriteln).toHaveBeenCalled();
    expect(mockWrite).toHaveBeenCalledWith("$ ");
  });

  it("reconnect button re-invokes getTtydUrl", async () => {
    mockGetTtydUrl.mockRejectedValue(new Error("still down"));
    renderAt();
    await waitFor(() => expect(mockGetTtydUrl).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /reconnect/i }));
    await waitFor(() => expect(mockGetTtydUrl).toHaveBeenCalledTimes(2));
  });

  it("attaches xterm to the websocket and flips to connected on open (W-3.1)", async () => {
    mockGetTtydUrl.mockResolvedValueOnce({ url: "ws://localhost:7681/ws" });
    renderAt("/terminal/vm-xyz");
    await waitFor(() => expect(wsInstances.length).toBe(1));
    const ws = wsInstances[0];
    expect(ws.url).toBe("ws://localhost:7681/ws");
    // ttyd subprotocol per the page contract — must be passed so the
    // server doesn't 400 the handshake.
    expect(ws.protocols).toEqual(["tty"]);
    // AttachAddon should have been instantiated with the WS
    expect(AttachAddon).toHaveBeenCalledWith(ws);
    // Simulate the server completing the handshake
    act(() => {
      ws.onopen?.();
    });
    await screen.findByText("connected");
  });

  it("shows the AGENT_PLATFORM_TTYD_MOCK_URL hint in the demo-mode card", async () => {
    mockGetTtydUrl.mockRejectedValueOnce(new Error("503 not configured"));
    renderAt();
    await screen.findByText("Demo mode");
    expect(
      screen.getByText(/AGENT_PLATFORM_TTYD_MOCK_URL=ws:\/\/localhost:7681\/ws/),
    ).toBeInTheDocument();
  });
});
