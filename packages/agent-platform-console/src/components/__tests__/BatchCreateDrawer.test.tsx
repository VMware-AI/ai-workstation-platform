import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BatchCreateDrawer } from "../BatchCreateDrawer";

const createDeploymentSpy = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      createDeployment: (...args: unknown[]) => createDeploymentSpy(...args),
    },
  };
});

beforeEach(() => {
  createDeploymentSpy.mockReset();
});

describe("BatchCreateDrawer (W-1)", () => {
  it("renders nothing when open=false", () => {
    render(<BatchCreateDrawer open={false} onClose={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the form fields when open", () => {
    render(<BatchCreateDrawer open onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: /Batch Create VMs/i })).toBeInTheDocument();
    expect(screen.getByText(/Tenant id/i)).toBeInTheDocument();
    expect(screen.getByText(/Template path/i)).toBeInTheDocument();
    expect(screen.getByText(/Image version/i)).toBeInTheDocument();
    expect(screen.getByText(/Users \(one per line\)/i)).toBeInTheDocument();
  });

  it("shows field errors and does not call API when required fields are empty", async () => {
    const user = userEvent.setup();
    render(<BatchCreateDrawer open onClose={() => {}} />);

    // tenant + image_version + users are empty (template has a placeholder default).
    await user.click(screen.getByRole("button", { name: /^Create$/i }));

    expect(screen.getByText(/Tenant id is required/i)).toBeInTheDocument();
    expect(screen.getByText(/Image version is required/i)).toBeInTheDocument();
    expect(screen.getByText(/At least one user required/i)).toBeInTheDocument();
    expect(createDeploymentSpy).not.toHaveBeenCalled();
  });

  it("splits the users textarea by line, derives intended_name, and calls createDeployment", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onCreated = vi.fn();
    createDeploymentSpy.mockResolvedValue({
      id: "d-001",
      tenant_id: "t-a",
      template: "x",
      image_version: "v0.1.0",
      state: "pending",
      counts: { requested: 2, succeeded: 0, failed: 0 },
      created_at: "",
      updated_at: "",
    });

    render(<BatchCreateDrawer open onClose={onClose} onCreated={onCreated} />);

    await user.type(screen.getByPlaceholderText(/acme-corp/i), "t-a");
    await user.type(screen.getByPlaceholderText(/v0\.1\.0/i), "v0.1.0");
    await user.type(
      screen.getByPlaceholderText(/alice/i),
      "alice\nbob",
    );

    await user.click(screen.getByRole("button", { name: /^Create$/i }));

    await waitFor(() => expect(createDeploymentSpy).toHaveBeenCalledTimes(1));
    const body = createDeploymentSpy.mock.calls[0][0];
    expect(body.tenant_id).toBe("t-a");
    expect(body.image_version).toBe("v0.1.0");
    expect(body.items).toEqual([
      { owner_id: "alice", intended_name: "alice-001" },
      { owner_id: "bob", intended_name: "bob-002" },
    ]);
    expect(onCreated).toHaveBeenCalledWith("d-001");
    expect(onClose).toHaveBeenCalled();
  });

  it("displays a submit error when the API rejects", async () => {
    const user = userEvent.setup();
    createDeploymentSpy.mockRejectedValue(new Error("422 image_version not registered"));

    render(<BatchCreateDrawer open onClose={() => {}} />);
    await user.type(screen.getByPlaceholderText(/acme-corp/i), "t-a");
    await user.type(screen.getByPlaceholderText(/v0\.1\.0/i), "v9.9.9");
    await user.type(screen.getByPlaceholderText(/alice/i), "alice");
    await user.click(screen.getByRole("button", { name: /^Create$/i }));

    await waitFor(() =>
      expect(screen.getByText(/422 image_version not registered/i)).toBeInTheDocument(),
    );
  });

  // doc 30 PR-Buf-1 polish (2026-06-01) — P-2 / P-3 client-side guards.

  describe("P-2 client-side guards", () => {
    it("rejects tenant ids that are not kebab-case", async () => {
      const user = userEvent.setup();
      render(<BatchCreateDrawer open onClose={() => {}} />);

      await user.type(screen.getByPlaceholderText(/acme-corp/i), "Acme Corp");
      await user.type(screen.getByPlaceholderText(/v0\.1\.0/i), "v0.1.0");
      await user.type(screen.getByPlaceholderText(/alice/i), "alice");
      await user.click(screen.getByRole("button", { name: /^Create$/i }));

      expect(
        screen.getByText(/Tenant id must be kebab-case/i),
      ).toBeInTheDocument();
      expect(createDeploymentSpy).not.toHaveBeenCalled();
    });

    it("flags duplicate users and blocks submit", async () => {
      const user = userEvent.setup();
      render(<BatchCreateDrawer open onClose={() => {}} />);

      await user.type(screen.getByPlaceholderText(/acme-corp/i), "t-a");
      await user.type(screen.getByPlaceholderText(/v0\.1\.0/i), "v0.1.0");
      await user.type(screen.getByPlaceholderText(/alice/i), "alice\nbob\nalice");
      await user.click(screen.getByRole("button", { name: /^Create$/i }));

      expect(screen.getByText(/Duplicate user: alice/i)).toBeInTheDocument();
      expect(createDeploymentSpy).not.toHaveBeenCalled();
    });

    it("previews the queued row count as the operator types", async () => {
      const user = userEvent.setup();
      render(<BatchCreateDrawer open onClose={() => {}} />);

      expect(screen.getByTestId("row-count-preview")).toHaveTextContent(
        /No VMs queued/i,
      );

      await user.type(screen.getByPlaceholderText(/alice/i), "alice\nbob\ncarol");
      expect(screen.getByTestId("row-count-preview")).toHaveTextContent(
        /Will create 3 VMs/i,
      );
    });
  });

  describe("P-3 retry on submit failure", () => {
    it("retains the form and re-submits when Retry is clicked", async () => {
      const user = userEvent.setup();
      createDeploymentSpy
        .mockRejectedValueOnce(new Error("503 backend cold start"))
        .mockResolvedValueOnce({
          id: "d-002",
          tenant_id: "t-a",
          template: "x",
          image_version: "v0.1.0",
          state: "pending",
          counts: { requested: 1, succeeded: 0, failed: 0 },
          created_at: "",
          updated_at: "",
        });

      const onCreated = vi.fn();
      render(<BatchCreateDrawer open onClose={() => {}} onCreated={onCreated} />);

      await user.type(screen.getByPlaceholderText(/acme-corp/i), "t-a");
      await user.type(screen.getByPlaceholderText(/v0\.1\.0/i), "v0.1.0");
      await user.type(screen.getByPlaceholderText(/alice/i), "alice");
      await user.click(screen.getByRole("button", { name: /^Create$/i }));

      // First attempt: error banner appears; form fields stay populated.
      await waitFor(() =>
        expect(screen.getByTestId("submit-error")).toHaveTextContent(
          /503 backend cold start/i,
        ),
      );

      await user.click(screen.getByRole("button", { name: /^Retry$/i }));

      await waitFor(() => expect(createDeploymentSpy).toHaveBeenCalledTimes(2));
      expect(onCreated).toHaveBeenCalledWith("d-002");
    });
  });
});
