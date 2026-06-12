import { NavLink, Outlet } from "react-router-dom";
import { BarChart3, ClipboardList, Cpu, PlusSquare } from "lucide-react";
import { cn } from "@/lib/cn";

// Portal is end-user facing — single-column sidebar, simpler chrome
// than the admin C2 console. SSO username placeholder lives in the
// header so users always know which identity is calling the backend.

const NAV = [
  { to: "/my-agents", label: "My Agents", icon: Cpu },
  { to: "/request", label: "Request Agent", icon: PlusSquare },
  { to: "/requests", label: "My Requests", icon: ClipboardList },
  { to: "/usage", label: "My Usage", icon: BarChart3 },
] as const;

function currentUser(): string {
  if (typeof window === "undefined") return "user";
  return window.sessionStorage?.getItem("agent-platform:user") ?? "user";
}

export default function Layout() {
  const user = currentUser();
  return (
    <div className="grid min-h-screen grid-cols-[220px_1fr] bg-background text-foreground">
      <aside className="border-r p-4">
        <div className="mb-6 px-2">
          <div className="text-lg font-semibold">🦞 Agent Platform</div>
          <div className="text-xs text-muted-foreground">Portal (C12)</div>
          <div className="mt-2 text-xs text-muted-foreground">
            signed in as <span className="font-mono">{user}</span>
          </div>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon }) => (
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
      </aside>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  );
}
