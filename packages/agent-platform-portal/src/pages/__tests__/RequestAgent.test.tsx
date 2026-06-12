import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RequestAgent from "../RequestAgent";

vi.mock("@/lib/api", () => ({
  api: { submitApproval: vi.fn() },
  ApiError: class extends Error {},
}));

describe("RequestAgent", () => {
  it("renders heading + form controls", () => {
    render(<RequestAgent />);
    expect(screen.getByText("Request Agent")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /submit request/i })).toBeInTheDocument();
  });

  it("lists the four agent-vm packages in the picker", () => {
    render(<RequestAgent />);
    expect(screen.getByRole("option", { name: "agent-vm-small" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "agent-vm-medium" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "agent-vm-large" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "agent-vm-gpu" })).toBeInTheDocument();
  });
});
