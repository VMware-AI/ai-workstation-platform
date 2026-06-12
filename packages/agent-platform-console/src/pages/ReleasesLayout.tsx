import { Outlet } from "react-router-dom";
import SubNav from "@/components/SubNav";

const SUB_TABS = [
  { to: "/releases/upgrades", label: "Upgrades" },
  { to: "/releases/images", label: "Image Versions" },
] as const;

// Wraps the Releases tab: upgrade lifecycle (already wired today, moved in R-4)
// and ImageVersions (placeholder per decision 4.2; full impl deferred to M2).
export default function ReleasesLayout() {
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">Releases</h1>
      <p className="text-sm text-muted-foreground">
        Blue/green upgrades and the image catalog.
      </p>
      <SubNav tabs={SUB_TABS} />
      <Outlet />
    </div>
  );
}
