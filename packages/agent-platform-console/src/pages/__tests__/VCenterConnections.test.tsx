import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import VCenterConnections from "../VCenterConnections";

const listVCenters = vi.fn();
const vcenterHealth = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listVCenters: (...args: unknown[]) => listVCenters(...args),
      vcenterHealth: (...args: unknown[]) => vcenterHealth(...args),
    },
  };
});

function render_() {
  return render(
    <MemoryRouter>
      <VCenterConnections />
    </MemoryRouter>,
  );
}

describe("VCenterConnections (R-3)", () => {
  it("renders the empty state when no vCenter is configured", async () => {
    listVCenters.mockResolvedValue({ vcenters: [], _single_only: true });
    render_();
    expect(await screen.findByText(/No vCenter configured/i)).toBeInTheDocument();
    expect(screen.getByText(/AGENT_PLATFORM_VCENTER_HOST/)).toBeInTheDocument();
  });

  it("renders a row per configured vCenter", async () => {
    listVCenters.mockResolvedValue({
      vcenters: [
        {
          name: "default",
          host: "vcsa-01.example.com",
          port: 443,
          user: "svc-agent-platform",
          verify_ssl: true,
          configured_via: "env",
        },
      ],
      _single_only: true,
    });
    render_();
    expect(await screen.findByText("vcsa-01.example.com")).toBeInTheDocument();
    expect(screen.getByText("svc-agent-platform")).toBeInTheDocument();
    expect(screen.getByText("unprobed")).toBeInTheDocument();
  });

  it("probes a vCenter on button click", async () => {
    listVCenters.mockResolvedValue({
      vcenters: [
        { name: "default", host: "vcsa-01.example.com", configured_via: "env" },
      ],
      _single_only: true,
    });
    vcenterHealth.mockResolvedValue({
      name: "default",
      host: "vcsa-01.example.com",
      status: "ok",
      api_version: "8.0.3",
    });
    render_();
    const probeBtn = await screen.findByRole("button", { name: /Probe/ });
    await userEvent.click(probeBtn);
    await waitFor(() => {
      expect(screen.getByText(/healthy/)).toBeInTheDocument();
    });
    expect(vcenterHealth).toHaveBeenCalledWith("default");
  });
});
