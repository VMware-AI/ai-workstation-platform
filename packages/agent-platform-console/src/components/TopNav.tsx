import { NavLink } from "react-router-dom";
import { Activity, BarChart3, Cloud, Rocket, Server } from "lucide-react";
import { cn } from "@/lib/cn";

// 5-tab horizontal top navigation per docs/architecture/21 §1.1.
// Each tab is a top-level admin area; sub-views render under via Outlet.
const TABS = [
  { to: "/overview", label: "Overview", icon: Activity },
  { to: "/lifecycle", label: "Lifecycle", icon: Server },
  { to: "/vcenter", label: "vCenter", icon: Cloud },
  { to: "/releases", label: "Releases", icon: Rocket },
  { to: "/operations", label: "Operations", icon: BarChart3 },
] as const;

export default function TopNav() {
  return (
    <header className="border-b bg-background">
      <div className="flex h-14 items-center gap-6 px-6">
        <div className="flex items-center gap-2">
          <span className="text-lg">🦞</span>
          <span className="text-sm font-semibold">Agent Platform Admin</span>
        </div>
        <nav className="flex gap-1">
          {TABS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-muted",
                  isActive && "bg-muted font-medium",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto text-xs text-muted-foreground">zw@local</div>
      </div>
    </header>
  );
}
