import { useEffect, useState } from "react";
import { api, type Health } from "@/lib/api";

// Bottom status strip: control-plane health + redesign hint.
// Lightweight per-Shell probe so it does not duplicate Overview's deeper aggregate (R-2).
export default function StatusBar() {
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const h: Health = await api.healthz();
        if (!cancelled) setHealthy(h.status === "ok");
      } catch {
        if (!cancelled) setHealthy(false);
      }
    };
    probe();
    const id = setInterval(probe, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const cpDot =
    healthy === null ? "bg-muted-foreground/40" : healthy ? "bg-emerald-500" : "bg-red-500";

  return (
    <footer className="border-t bg-muted/30 px-6 py-2 text-xs text-muted-foreground">
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${cpDot}`} aria-hidden />
          control-plane {healthy === null ? "checking…" : healthy ? "healthy" : "unreachable"}
        </span>
        <span className="ml-auto">redesign 2026-05-30 · M1 demo build</span>
      </div>
    </footer>
  );
}
