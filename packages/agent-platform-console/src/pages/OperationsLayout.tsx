import { Outlet } from "react-router-dom";
import SubNav from "@/components/SubNav";

const SUB_TABS = [
  { to: "/operations/audit", label: "Audit" },
  { to: "/operations/token-usage", label: "Token Usage" },
  { to: "/operations/components", label: "Components" },
] as const;

// Wraps the Operations tab: audit log, token usage, components health.
// Sub-pages are existing C2 pages (audit, token-usage) plus a new
// ComponentsHealth view that aggregates /admin/components/health (R-2).
export default function OperationsLayout() {
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">Operations</h1>
      <p className="text-sm text-muted-foreground">
        Audit trail, token spend, and platform-component readiness.
      </p>
      <SubNav tabs={SUB_TABS} />
      <Outlet />
    </div>
  );
}
