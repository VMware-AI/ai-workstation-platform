import { Outlet } from "react-router-dom";
import TopNav from "./TopNav";
import StatusBar from "./StatusBar";

// Top-level chrome: TopNav + main content area + StatusBar.
// Replaces the previous left-sidebar Layout.tsx as part of the
// admin console redesign (docs/architecture/21 §1.1).
export default function Shell() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <TopNav />
      <main className="flex-1 px-6 py-6">
        <Outlet />
      </main>
      <StatusBar />
    </div>
  );
}
