import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Upgrades from "../Upgrades";
import { mockUpgrades } from "@/lib/api/upgrades";

const listSpy = vi.fn();
const planSpy = vi.fn();
const startSpy = vi.fn();
const cutoverSpy = vi.fn();
const rollbackSpy = vi.fn();
const cleanupSpy = vi.fn();

vi.mock("@/lib/api/upgrades", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/upgrades")>("@/lib/api/upgrades");
  return {
    ...actual,
    upgradesApi: {
      list: (...args: unknown[]) => listSpy(...args),
      get: vi.fn(),
      plan: (...args: unknown[]) => planSpy(...args),
      start: (...args: unknown[]) => startSpy(...args),
      cutover: (...args: unknown[]) => cutoverSpy(...args),
      rollback: (...args: unknown[]) => rollbackSpy(...args),
      cleanup: (...args: unknown[]) => cleanupSpy(...args),
    },
  };
});

beforeEach(() => {
  listSpy.mockReset();
  planSpy.mockReset();
  startSpy.mockReset();
  cutoverSpy.mockReset();
  rollbackSpy.mockReset();
  cleanupSpy.mockReset();
});

describe("Upgrades page", () => {
  it("renders upgrade rows + demo badge when API returns _mock: true", async () => {
    listSpy.mockResolvedValue({ upgrades: mockUpgrades(), _mock: true });

    render(<Upgrades />);

    expect(screen.getByRole("heading", { name: "Upgrades" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("demo data")).toBeInTheDocument();
    });
    // 5 fixture rows
    expect(screen.getByText("upg-0001", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("upg-0005", { exact: false })).toBeInTheDocument();
    // state badges
    expect(screen.getByText("blue_ready")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("only shows Cutover on blue_ready rows and hides all actions on completed", async () => {
    listSpy.mockResolvedValue({ upgrades: mockUpgrades(), _mock: true });
    render(<Upgrades />);

    await waitFor(() => expect(screen.getByText("blue_ready")).toBeInTheDocument());

    // blue_ready row (upg-0001) — has Cutover and Rollback
    const blueReadyRow = screen.getByText("blue_ready").closest("tr");
    expect(blueReadyRow).not.toBeNull();
    expect(within(blueReadyRow!).getByRole("button", { name: "Cutover" })).toBeInTheDocument();
    expect(within(blueReadyRow!).getByRole("button", { name: "Rollback" })).toBeInTheDocument();

    // cleanup_pending row (upg-0003) — Cleanup only
    const cleanupRow = screen.getByText("cleanup_pending").closest("tr");
    expect(cleanupRow).not.toBeNull();
    expect(within(cleanupRow!).getByRole("button", { name: "Cleanup" })).toBeInTheDocument();
    expect(within(cleanupRow!).queryByRole("button", { name: "Cutover" })).toBeNull();

    // completed row (upg-0004) — no destructive actions
    const completedRow = screen.getByText("completed").closest("tr");
    expect(completedRow).not.toBeNull();
    expect(within(completedRow!).queryByRole("button", { name: "Cutover" })).toBeNull();
    expect(within(completedRow!).queryByRole("button", { name: "Rollback" })).toBeNull();
    expect(within(completedRow!).queryByRole("button", { name: "Cleanup" })).toBeNull();
  });

  it("cutover dialog blocks submission until the confirm checkbox is checked", async () => {
    listSpy.mockResolvedValue({ upgrades: mockUpgrades(), _mock: true });
    cutoverSpy.mockResolvedValue({ ...mockUpgrades()[0], state: "cutover_in_progress" });

    render(<Upgrades />);

    await waitFor(() => expect(screen.getByText("blue_ready")).toBeInTheDocument());

    const blueReadyRow = screen.getByText("blue_ready").closest("tr")!;
    fireEvent.click(within(blueReadyRow).getByRole("button", { name: "Cutover" }));

    // Dialog opens
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/Cut over upgrade upg-0001/, { exact: false }),
    ).toBeInTheDocument();

    // Confirm button is disabled before checkbox
    const confirmBtn = within(dialog).getByRole("button", { name: "Confirm" });
    expect(confirmBtn).toBeDisabled();

    // Clicking the disabled button does nothing
    fireEvent.click(confirmBtn);
    expect(cutoverSpy).not.toHaveBeenCalled();

    // Check the box
    fireEvent.click(within(dialog).getByLabelText("I understand the risk"));
    expect(confirmBtn).toBeEnabled();

    // Now submit
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(cutoverSpy).toHaveBeenCalledWith("upg-0001", true));
  });

  it("cleanup dialog requires dry-run before allowing destructive confirm", async () => {
    const fixtures = mockUpgrades();
    listSpy.mockResolvedValue({ upgrades: fixtures, _mock: true });
    cleanupSpy.mockResolvedValue({ ...fixtures[2] }); // upg-0003

    render(<Upgrades />);

    await waitFor(() => expect(screen.getByText("cleanup_pending")).toBeInTheDocument());

    const cleanupRow = screen.getByText("cleanup_pending").closest("tr")!;
    fireEvent.click(within(cleanupRow).getByRole("button", { name: "Cleanup" }));

    const dialog = await screen.findByRole("dialog");
    // Initially no confirm checkbox — must dry-run first
    expect(within(dialog).queryByLabelText("I understand the risk")).toBeNull();

    const dryRunBtn = within(dialog).getByRole("button", { name: "Run dry-run" });
    fireEvent.click(dryRunBtn);

    await waitFor(() =>
      expect(cleanupSpy).toHaveBeenCalledWith("upg-0003", false, true),
    );

    // After preview, the confirm checkbox + Confirm button appear.
    const updatedDialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(updatedDialog).getByText(/Dry-run preview/)).toBeInTheDocument(),
    );
    const confirmCheckbox = within(updatedDialog).getByLabelText("I understand the risk");
    fireEvent.click(confirmCheckbox);

    cleanupSpy.mockClear();
    cleanupSpy.mockResolvedValue({ ...fixtures[2], state: "completed" });
    fireEvent.click(within(updatedDialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(cleanupSpy).toHaveBeenCalledWith("upg-0003", true, false),
    );
  });

  it("plan dialog previews then starts an upgrade", async () => {
    listSpy.mockResolvedValue({ upgrades: [], _mock: false });
    planSpy.mockResolvedValue({
      tenant_id: "acme",
      from_version: "v0.1.0",
      to_version: "v0.2.0",
      vm_count: 2,
      blue_vm_names: ["wkst-alice-v0.2.0", "wkst-bob-v0.2.0"],
      estimated_seconds: 600,
      home_volume_strategy: "copy",
      dry_run: true,
      _mock: true,
    });
    startSpy.mockResolvedValue({
      ...mockUpgrades()[0],
      state: "planned",
    });

    render(<Upgrades />);

    await waitFor(() => expect(listSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Plan upgrade" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByPlaceholderText("acme"), {
      target: { value: "acme" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Preview plan" }));

    await waitFor(() => expect(planSpy).toHaveBeenCalled());
    expect(within(dialog).getByText("Plan preview")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Start upgrade" }));
    await waitFor(() => expect(startSpy).toHaveBeenCalled());
  });

  it("surfaces an error when list rejects", async () => {
    listSpy.mockRejectedValue(new Error("boom"));
    render(<Upgrades />);
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });
});
