"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { fetchJson } from "@/lib/fetchJson";

export default function LoginPage() {
  const router = useRouter();
  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const r = await fetchJson("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (r.ok) {
        router.push("/dashboard");
      } else {
        setError(r.error || "登录失败");
      }
    } finally {
      // Always re-enable the button — a network error must not strand it.
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <span className="text-5xl">🖥️</span>
          <h1 className="text-2xl font-bold text-gray-900 mt-2">Agent Platform</h1>
          <p className="text-gray-500 text-sm mt-1">AI Workstation 管理平台</p>
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">登录</h2>
          {error && <div className="bg-red-50 text-red-600 text-sm rounded p-2 mb-4">{error}</div>}
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">邮箱</label>
              <Input type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} required />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">密码</label>
              <Input type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} required />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>{loading ? "登录中..." : "登录"}</Button>
          </form>
          <p className="text-sm text-center text-gray-500 mt-4">
            没有账号？<Link href="/register" className="text-blue-600 hover:underline">注册</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
