"use client";
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearTokens } from "@/lib/auth";
import RequireAuth from "./RequireAuth";
import { apiFetch } from "@/lib/api";

interface UserProfile {
  id: string;
  name: string;
  email: string;
  preferred_currency: string;
  monthly_income_goal: number | null;
  display_title: string | null;
}

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

const nav = [
  { href: "/dashboard",       label: "Dashboard",       icon: "▦" },
  { href: "/accounts",        label: "Accounts",         icon: "◈" },
  { href: "/import",          label: "Import",           icon: "↑" },
  { href: "/transactions",    label: "Transactions",     icon: "≡" },
  { href: "/categories",      label: "Categories",       icon: "⊞" },
  { href: "/budgets",         label: "Budgets",          icon: "◎" },
  { href: "/analytics",       label: "Analytics",        icon: "◔" },
  { href: "/alerts",          label: "Alerts",           icon: "◇" },
  { href: "/reports",         label: "Reports",          icon: "⊜" },
  { href: "/jobs",            label: "Jobs",             icon: "⚙" },
  { href: "/advisor",         label: "Advisor",          icon: "◈" },
  { href: "/recommendations", label: "Invest",           icon: "▲" },
  { href: "/profile",         label: "Profile",          icon: "○" },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [profile, setProfile] = useState<UserProfile | null>(null);

  useEffect(() => {
    apiFetch<UserProfile>("/api/v1/me")
      .then(setProfile)
      .catch(() => {
        // Non-fatal: sidebar identity will just be absent
      });
  }, []);

  function logout() {
    clearTokens();
    router.replace("/login");
  }

  return (
    <RequireAuth>
      <div className="flex min-h-screen">
        <aside className="flex w-56 flex-col border-r border-neutral-200 bg-white">
          <div className="px-5 py-6">
            <Link href="/dashboard" className="text-lg font-bold text-neutral-900">
              BudgetFlow
            </Link>
          </div>

          <nav className="flex-1 space-y-0.5 px-3">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  pathname.startsWith(n.href)
                    ? "bg-neutral-100 text-neutral-900"
                    : "text-neutral-500 hover:bg-neutral-50 hover:text-neutral-800"
                }`}
              >
                <span className="text-base">{n.icon}</span>
                {n.label}
              </Link>
            ))}
          </nav>

          <div className="border-t border-neutral-200 p-3 space-y-1">
            {/* User identity block */}
            {profile ? (
              <Link
                href="/profile"
                className="flex items-center gap-2 rounded-lg px-3 py-2 hover:bg-neutral-50 transition-colors"
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-neutral-900 text-xs font-semibold text-white">
                  {initials(profile.name)}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-neutral-900">{profile.name}</p>
                  <p className="truncate text-xs text-neutral-400">{profile.email}</p>
                </div>
              </Link>
            ) : (
              <div className="flex items-center gap-2 px-3 py-2">
                <span className="h-7 w-7 rounded-full bg-neutral-200 animate-pulse" />
                <div className="flex-1 space-y-1">
                  <div className="h-2.5 w-20 rounded bg-neutral-200 animate-pulse" />
                  <div className="h-2 w-28 rounded bg-neutral-100 animate-pulse" />
                </div>
              </div>
            )}

            <button
              onClick={logout}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-neutral-500 hover:bg-neutral-50 hover:text-neutral-800"
            >
              <span className="text-base">⎋</span>
              Logout
            </button>
          </div>
        </aside>

        <main className="flex-1 overflow-auto p-8">{children}</main>
      </div>
    </RequireAuth>
  );
}
