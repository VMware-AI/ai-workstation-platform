import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * Minimal right-side drawer. No @radix-ui/react-dialog dependency — we own
 * focus-trap simulation via the close button only and rely on the Escape key
 * handler for keyboard close. Sufficient for the W-1 batch create surface;
 * upgrade to radix when a second drawer use-case lands.
 */
export type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
};

export function Drawer({ open, onClose, title, children }: DrawerProps) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <div
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
        data-testid="drawer-overlay"
      />
      <div
        className={cn(
          "absolute right-0 top-0 flex h-full w-full max-w-md flex-col",
          "bg-background border-l border-border shadow-xl",
        )}
      >
        <div className="flex items-center justify-between border-b px-6 py-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  );
}
