import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Platform",
  description: "AI Workstation Platform — agent VM 发布与管理",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
