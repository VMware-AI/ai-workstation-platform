import { cn } from "@/lib/cn";
import type { MyApproval } from "@/lib/api";

// W-4: 4-step timeline derived purely from an approval row's state +
// decided_at. Pre-#136 the post-decision states (provisioning / ready)
// aren't tracked yet; this component renders them as "pending" rather
// than fabricating data.

type Step = {
  key: "submitted" | "decided" | "provisioning" | "ready";
  label: string;
};

const STEPS: readonly Step[] = [
  { key: "submitted", label: "Submitted" },
  { key: "decided", label: "Decided" },
  { key: "provisioning", label: "Provisioning" },
  { key: "ready", label: "Ready" },
];

function reachedThrough(approval: MyApproval): Step["key"] {
  // Walk forward as far as the data justifies; never further.
  if (approval.state === "pending") return "submitted";
  // approved or rejected → decided is the furthest we can claim today
  return "decided";
}

export interface RequestTimelineProps {
  approval: MyApproval;
}

export function RequestTimeline({ approval }: RequestTimelineProps) {
  const last = reachedThrough(approval);
  const lastIdx = STEPS.findIndex((s) => s.key === last);
  const rejected = approval.state === "rejected";
  return (
    <ol
      data-testid="request-timeline"
      className="mt-2 flex items-start gap-4 text-xs"
    >
      {STEPS.map((step, i) => {
        const reached = i <= lastIdx;
        const isCurrent = i === lastIdx;
        const dotTone = rejected && isCurrent
          ? "bg-destructive"
          : reached
            ? "bg-emerald-500"
            : "bg-muted";
        return (
          <li key={step.key} className="flex flex-1 flex-col items-center text-center">
            <span
              className={cn("h-3 w-3 rounded-full", dotTone)}
              data-testid={`timeline-dot-${step.key}`}
              data-reached={reached ? "true" : "false"}
            />
            <span
              className={cn(
                "mt-1",
                reached ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {step.label}
            </span>
            {step.key === "submitted" && (
              <time className="text-muted-foreground tabular-nums">
                {new Date(approval.created_at).toLocaleDateString()}
              </time>
            )}
            {step.key === "decided" && approval.decided_at && (
              <time className="text-muted-foreground tabular-nums">
                {new Date(approval.decided_at).toLocaleDateString()}
              </time>
            )}
          </li>
        );
      })}
    </ol>
  );
}
