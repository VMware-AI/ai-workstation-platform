import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RequestTimeline } from "../RequestTimeline";
import type { MyApproval } from "@/lib/api";

const base: MyApproval = {
  id: 1,
  requester: "alice",
  package: "agent-vm-small",
  justification: "for the demo",
  state: "pending",
  created_at: "2026-05-30T08:00:00Z",
  decided_at: null,
  decided_by: null,
  decision_reason: null,
};

function reached(label: string): boolean {
  return (
    screen.getByTestId(`timeline-dot-${label}`).getAttribute("data-reached") === "true"
  );
}

describe("RequestTimeline — W-4", () => {
  it("lights only the submitted step when state=pending", () => {
    render(<RequestTimeline approval={base} />);
    expect(reached("submitted")).toBe(true);
    expect(reached("decided")).toBe(false);
    expect(reached("provisioning")).toBe(false);
    expect(reached("ready")).toBe(false);
  });

  it("lights submitted + decided when state=approved", () => {
    render(
      <RequestTimeline
        approval={{ ...base, state: "approved", decided_at: "2026-05-30T09:00:00Z" }}
      />,
    );
    expect(reached("submitted")).toBe(true);
    expect(reached("decided")).toBe(true);
    // M1 pre-#136: post-decision states stay unlit rather than fabricated
    expect(reached("provisioning")).toBe(false);
    expect(reached("ready")).toBe(false);
  });

  it("lights submitted + decided when state=rejected (decided is the terminal lit step)", () => {
    render(
      <RequestTimeline
        approval={{ ...base, state: "rejected", decided_at: "2026-05-30T09:00:00Z" }}
      />,
    );
    expect(reached("submitted")).toBe(true);
    expect(reached("decided")).toBe(true);
  });

  it("renders a date for the decided step when present", () => {
    render(
      <RequestTimeline
        approval={{ ...base, state: "approved", decided_at: "2026-05-30T09:00:00Z" }}
      />,
    );
    // both submitted and decided dates render — at least 2 <time> nodes
    expect(screen.getAllByText(/2026/).length).toBeGreaterThanOrEqual(2);
  });
});
