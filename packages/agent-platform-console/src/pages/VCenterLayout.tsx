import { Outlet, useParams } from "react-router-dom";
import SubNav from "@/components/SubNav";

// Wraps the vCenter tab. SubNav adapts to whether a specific vCenter name
// is in the URL (`:name/inventory`, `:name/templates`) — the Connections
// tab itself does not take a name parameter.
export default function VCenterLayout() {
  const { name } = useParams();
  const tabs = [
    { to: "/vcenter/connections", label: "Connections" },
    { to: name ? `/vcenter/${name}/inventory` : "/vcenter/connections", label: "Inventory" },
    { to: name ? `/vcenter/${name}/templates` : "/vcenter/connections", label: "Templates" },
  ];
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">vCenter</h1>
      <p className="text-sm text-muted-foreground">
        Read-only inventory and template surface. M1 supports a single vCenter from environment;
        multi-vCenter via config.yaml lands in M2 (see doc 21 §4).
      </p>
      <SubNav tabs={tabs} />
      <Outlet />
    </div>
  );
}
