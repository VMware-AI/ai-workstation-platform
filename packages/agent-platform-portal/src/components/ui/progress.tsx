import * as React from "react";
import { cn } from "@/lib/cn";

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  max?: number;
  // When ratio crosses these thresholds the bar shifts color. Defaults align
  // with the M1 quota soft policy: < 80% green, 80-99% amber, ≥ 100% red.
  warnAt?: number;
  dangerAt?: number;
}

export function Progress({
  value,
  max = 100,
  warnAt = 0.8,
  dangerAt = 1.0,
  className,
  ...props
}: ProgressProps) {
  const ratio = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  const pct = Math.round(ratio * 100);
  const tone =
    ratio >= dangerAt
      ? "bg-destructive"
      : ratio >= warnAt
        ? "bg-amber-500"
        : "bg-emerald-500";
  return (
    <div
      role="progressbar"
      aria-valuenow={value}
      aria-valuemax={max}
      aria-valuemin={0}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
      {...props}
    >
      <div
        data-testid="progress-fill"
        className={cn("h-full transition-all", tone)}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
