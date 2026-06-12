import { Outlet } from "react-router-dom";
import SubNav from "@/components/SubNav";

const SUB_TABS = [
  { to: "/lifecycle/vms", label: "VMs" },
  { to: "/lifecycle/deployments", label: "Deployments" },
  { to: "/lifecycle/approvals", label: "Approvals" },
] as const;

// Wraps the Lifecycle tab's three sub-views with a shared header + SubNav.
// Sub-page content is plugged in by R-4 (vms/approvals migration) and R-5 (deployments).
export default function LifecycleLayout() {
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">Lifecycle</h1>
      <p className="text-sm text-muted-foreground">
        VMs, deployments, and approvals — the day-to-day plumbing for admin oversight.
      </p>
      <SubNav tabs={SUB_TABS} />
      <Outlet />
    </div>
  );
}
