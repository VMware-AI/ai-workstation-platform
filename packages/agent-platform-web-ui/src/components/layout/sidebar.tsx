"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  Cpu,
  Zap,
  BarChart2,
  ScrollText,
  CreditCard,
  LogOut,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/dashboard", label: "总览", icon: LayoutDashboard },
  { href: "/instances", label: "实例", icon: Cpu },
  { href: "/models", label: "模型接入", icon: Zap },
  { href: "/compute-pools", label: "计算池", icon: Server },
  { href: "/billing", label: "计费用量", icon: CreditCard },
  { href: "/logs", label: "日志", icon: ScrollText },
  { href: "/monitoring", label: "监控", icon: BarChart2 },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  return (
    <div className="flex h-full w-56 flex-col bg-gray-900 text-white">
      <div className="flex items-center gap-2 px-4 py-5 border-b border-gray-700">
        <span className="text-2xl">🖥️</span>
        <span className="text-lg font-bold text-white">Agent Platform</span>
      </div>
      <nav className="flex-1 overflow-y-auto py-4 px-2">
        {nav.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors mb-1",
              pathname === href || pathname.startsWith(href + "/")
                ? "bg-blue-600 text-white"
                : "text-gray-300 hover:bg-gray-800 hover:text-white"
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="border-t border-gray-700 p-2">
        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-gray-300 hover:bg-gray-800 hover:text-white transition-colors"
        >
          <LogOut className="h-4 w-4" />
          退出登录
        </button>
      </div>
    </div>
  );
}
