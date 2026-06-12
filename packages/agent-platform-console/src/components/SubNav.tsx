import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";

// Pill-style sub-navigation rendered inside each top tab layout.
// Built on react-router NavLink for active-state styling.
export type SubTab = { to: string; label: string };

export default function SubNav({ tabs }: { tabs: readonly SubTab[] }) {
  return (
    <nav className="mb-6 flex gap-1 border-b">
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          end
          className={({ isActive }) =>
            cn(
              "px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground",
              isActive && "border-b-2 border-primary text-foreground",
            )
          }
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}
