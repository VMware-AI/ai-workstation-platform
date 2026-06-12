import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VMs from "../VMs";

const listVmsSpy = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listVms: (...args: unknown[]) => listVmsSpy(...args),
    },
  };
});

beforeEach(() => {
  listVmsSpy.mockReset();
  listVmsSpy.mockResolvedValue({ vms: [], _stub: true });
});

function renderVMs() {
  return render(
    <MemoryRouter>
      <VMs />
    </MemoryRouter>,
  );
}

describe("VMs page (W-1 wire)", () => {
  it("renders the Batch Create button", async () => {
    renderVMs();
    expect(screen.getByRole("button", { name: /Batch Create/i })).toBeInTheDocument();
    await waitFor(() => expect(listVmsSpy).toHaveBeenCalled());
  });

  it("opens the BatchCreateDrawer when the button is clicked", async () => {
    const user = userEvent.setup();
    renderVMs();
    await waitFor(() => expect(listVmsSpy).toHaveBeenCalled());

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Batch Create/i }));
    expect(screen.getByRole("dialog", { name: /Batch Create VMs/i })).toBeInTheDocument();
  });
});
