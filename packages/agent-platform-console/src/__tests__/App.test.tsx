import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../App";

// Stub the C1 probes used by Overview + StatusBar so the test focuses on
// route + IA wiring, not network behavior.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      healthz: vi.fn().mockResolvedValue({ status: "ok" }),
      version: vi.fn().mockResolvedValue({ version: "test-1.2.3" }),
    },
  };
});

const TABS = ["Overview", "Lifecycle", "vCenter", "Releases", "Operations"];

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App shell (R-1)", () => {
  it("renders the 5-tab TopNav on every route", () => {
    renderAt("/overview");
    for (const label of TABS) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("renders Overview at /overview and shows control-plane probe", async () => {
    renderAt("/overview");
    expect(screen.getByRole("heading", { name: /Overview/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("healthy")).toBeInTheDocument();
    });
  });

  it("redirects root / to /overview", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { name: /Overview/i })).toBeInTheDocument();
  });

  it("renders LifecycleLayout with SubNav at /lifecycle/vms", () => {
    renderAt("/lifecycle/vms");
    expect(screen.getByRole("heading", { name: /Lifecycle/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^VMs$/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Deployments$/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Approvals$/ })).toBeInTheDocument();
  });

  it("renders the Image Versions M2 placeholder", () => {
    renderAt("/releases/images");
    expect(screen.getByText(/deferred to M2/i)).toBeInTheDocument();
  });

  it("redirects legacy /vms to /lifecycle/vms (R-4 migration)", () => {
    renderAt("/vms");
    // After R-4, /vms is a 301 → /lifecycle/vms, which renders LifecycleLayout
    expect(screen.getByRole("heading", { name: /Lifecycle/ })).toBeInTheDocument();
  });

  it("redirects legacy /dashboard to /overview after R-4", () => {
    renderAt("/dashboard");
    expect(screen.getByRole("heading", { name: /Overview/i })).toBeInTheDocument();
  });
});
