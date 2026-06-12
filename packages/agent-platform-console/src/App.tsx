import { Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import TabStub from "./components/TabStub";
import Overview from "./pages/Overview";
import LifecycleLayout from "./pages/LifecycleLayout";
import VCenterLayout from "./pages/VCenterLayout";
import VCenterConnections from "./pages/VCenterConnections";
import VCenterInventory from "./pages/VCenterInventory";
import VCenterTemplates from "./pages/VCenterTemplates";
import ReleasesLayout from "./pages/ReleasesLayout";
import OperationsLayout from "./pages/OperationsLayout";
import ComponentsHealth from "./pages/ComponentsHealth";
import Deployments from "./pages/Deployments";
import DeploymentDetail from "./pages/DeploymentDetail";

// Pages migrated by R-4 from legacy /dashboard·/vms·/approvals·/audit·/token-usage·/upgrades
// paths into the new IA. Legacy paths now Navigate-redirect to the new homes.
// Dashboard.tsx is deleted; Overview.tsx supersedes it.
import VMs from "./pages/VMs";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import TokenUsage from "./pages/TokenUsage";
import Upgrades from "./pages/Upgrades";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        {/* Default landing */}
        <Route path="/" element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<Overview />} />

        {/* Lifecycle tab */}
        <Route path="/lifecycle" element={<LifecycleLayout />}>
          <Route index element={<Navigate to="vms" replace />} />
          <Route path="vms" element={<VMs />} />
          <Route path="deployments" element={<Deployments />} />
          <Route path="deployments/:id" element={<DeploymentDetail />} />
          <Route path="approvals" element={<Approvals />} />
        </Route>

        {/* vCenter tab — R-3 wired */}
        <Route path="/vcenter" element={<VCenterLayout />}>
          <Route index element={<Navigate to="connections" replace />} />
          <Route path="connections" element={<VCenterConnections />} />
          <Route path=":name/inventory" element={<VCenterInventory />} />
          <Route path=":name/templates" element={<VCenterTemplates />} />
        </Route>

        {/* Releases tab */}
        <Route path="/releases" element={<ReleasesLayout />}>
          <Route index element={<Navigate to="upgrades" replace />} />
          <Route path="upgrades" element={<Upgrades />} />
          <Route
            path="images"
            element={
              <TabStub
                title="Image Versions"
                pr="M2"
                details="ImageVersion table + promote workflow deferred to M2 per decision 4.2."
              />
            }
          />
        </Route>

        {/* Operations tab */}
        <Route path="/operations" element={<OperationsLayout />}>
          <Route index element={<Navigate to="audit" replace />} />
          <Route path="audit" element={<Audit />} />
          <Route path="token-usage" element={<TokenUsage />} />
          <Route path="components" element={<ComponentsHealth />} />
        </Route>

        {/* Legacy → new-IA redirects (per docs/architecture/21 §4.4 — 渐进 migration).
            Bookmarks and the old M1-demo runbook keep working. */}
        <Route path="/dashboard" element={<Navigate to="/overview" replace />} />
        <Route path="/vms" element={<Navigate to="/lifecycle/vms" replace />} />
        <Route path="/approvals" element={<Navigate to="/lifecycle/approvals" replace />} />
        <Route path="/audit" element={<Navigate to="/operations/audit" replace />} />
        <Route
          path="/token-usage"
          element={<Navigate to="/operations/token-usage" replace />}
        />
        <Route path="/upgrades" element={<Navigate to="/releases/upgrades" replace />} />

        <Route path="*" element={<div className="p-8">Not found</div>} />
      </Route>
    </Routes>
  );
}
