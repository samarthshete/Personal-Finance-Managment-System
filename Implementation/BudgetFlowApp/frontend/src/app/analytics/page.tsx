"use client";
import { useEffect, useState, useCallback } from "react";
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import AppShell from "@/components/AppShell";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";
import Alert from "@/components/ui/Alert";
import Spinner from "@/components/ui/Spinner";
import EmptyState from "@/components/ui/EmptyState";
import PageHeader from "@/components/ui/PageHeader";
import { apiFetch, ApiError } from "@/lib/api";

interface Summary {
  total_spending: number;
  by_category: { category_id: string | null; category_name?: string | null; category_type?: string | null; total: number }[];
  by_account: { account_id: string; total: number }[];
}
interface TrendPoint { period: string; total: number }
interface BvaRow { category_id: string; limit_amount: number; spent_amount: number; percent: number }
interface Budget { id: string; name: string }
interface Category { id: string; name: string }

const groupOpts = [
  { value: "day", label: "Day" }, { value: "week", label: "Week" }, { value: "month", label: "Month" },
];

export default function AnalyticsPage() {
  const now = new Date();
  const [dateFrom, setDateFrom] = useState(new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10));
  const [dateTo, setDateTo] = useState(now.toISOString().slice(0, 10));
  const [groupBy, setGroupBy] = useState("month");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [selectedBudget, setSelectedBudget] = useState("");
  const [bva, setBva] = useState<BvaRow[]>([]);
  const [bvaError, setBvaError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const catMap = Object.fromEntries(categories.map(c => [c.id, c.name]));

  const loadMain = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [s, t, b, c] = await Promise.all([
        apiFetch<Summary>(`/api/v1/analytics/summary?date_from=${dateFrom}&date_to=${dateTo}`),
        apiFetch<TrendPoint[]>(`/api/v1/analytics/trends?date_from=${dateFrom}&date_to=${dateTo}&group_by=${groupBy}`),
        apiFetch<Budget[]>("/api/v1/budgets"),
        apiFetch<Category[]>("/api/v1/categories"),
      ]);
      setSummary(s); setTrends(t); setBudgets(b); setCategories(c);
    } catch (e) { setError((e as ApiError).detail); }
    setLoading(false);
  }, [dateFrom, dateTo, groupBy]);

  useEffect(() => { loadMain(); }, [loadMain]);

  useEffect(() => {
    if (!selectedBudget) { setBva([]); setBvaError(""); return; }
    setBvaError("");
    apiFetch<BvaRow[]>(`/api/v1/analytics/budget-vs-actual?budget_id=${selectedBudget}`)
      .then(setBva)
      .catch((e) => { setBva([]); setBvaError((e as ApiError).detail || "Failed to load budget comparison"); });
  }, [selectedBudget]);

  const trendData = trends.map(t => ({ period: String(t.period), amount: Number(t.total) }));
  const budgetOpts = [{ value: "", label: "Select budget..." }, ...budgets.map(b => ({ value: b.id, label: b.name }))];

  return (
    <AppShell>
      <PageHeader title="Analytics" />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      <Card className="mb-6">
        <div className="flex flex-wrap items-end gap-3">
          <Input label="From" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
          <Input label="To" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
          <Select label="Group by" options={groupOpts} value={groupBy} onChange={e => setGroupBy(e.target.value)} />
          <Button onClick={loadMain} variant="secondary">Refresh</Button>
        </div>
      </Card>

      {loading ? <Spinner /> : !summary ? (
        <p className="text-sm text-neutral-400">No data available for the selected range.</p>
      ) : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-3">
            <Card>
              <p className="text-sm text-neutral-500">Total Spending</p>
              <p className="text-2xl font-bold">${Number(summary.total_spending).toFixed(2)}</p>
            </Card>
            <Card>
              <p className="text-sm text-neutral-500">Categories</p>
              <p className="text-2xl font-bold">{summary.by_category.length}</p>
            </Card>
            <Card>
              <p className="text-sm text-neutral-500">Accounts</p>
              <p className="text-2xl font-bold">{summary.by_account.length}</p>
            </Card>
          </div>

          {summary.by_category.length > 0 && (
            <Card className="mb-6">
              <h3 className="mb-3 text-sm font-medium text-neutral-700">Spending by Category</h3>
              <div className="space-y-2">
                {summary.by_category.map((c, i) => {
                  const pct = Number(summary.total_spending) > 0
                    ? Math.min((Number(c.total) / Number(summary.total_spending)) * 100, 100)
                    : 0;
                  return (
                    <div key={i} className="flex items-center gap-3">
                      <span className="w-32 truncate text-xs text-neutral-600">
                        {c.category_name ?? (c.category_id ? (catMap[c.category_id] || "Unknown") : "Uncategorized")}
                      </span>
                      <div className="flex-1 rounded-full bg-neutral-100 h-3">
                        <div className="h-3 rounded-full bg-neutral-700" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-xs font-medium tabular-nums">${Number(c.total).toFixed(2)}</span>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          <Card className="mb-6">
            <h3 className="mb-3 text-sm font-medium text-neutral-700">Spending Trend</h3>
            {trendData.length === 0 ? (
              <EmptyState message="No trend data for the selected range" />
            ) : trendData.length === 1 ? (
              <div className="flex items-center justify-center py-10">
                <div className="text-center">
                  <p className="text-xs text-neutral-500">Single period</p>
                  <p className="text-sm font-medium text-neutral-700">{trendData[0].period}</p>
                  <p className="mt-1 text-3xl font-bold text-neutral-900">${trendData[0].amount.toFixed(2)}</p>
                </div>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" tick={{ fontSize: 12 }} />
                  <YAxis
                    tick={{ fontSize: 12 }}
                    domain={[
                      (min: number) => Math.max(0, Math.floor(min * 0.9)),
                      (max: number) => Math.ceil(max * 1.1),
                    ]}
                  />
                  <Tooltip formatter={(v) => [`$${Number(v).toFixed(2)}`, "Spending"]} />
                  <Line
                    type="monotone"
                    dataKey="amount"
                    stroke="#111827"
                    strokeWidth={2}
                    dot={{ r: 4 }}
                    activeDot={{ r: 6 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </Card>

          <Card>
            <h3 className="mb-3 text-sm font-medium text-neutral-700">Budget vs Actual</h3>
            <Select options={budgetOpts} value={selectedBudget} onChange={e => setSelectedBudget(e.target.value)} />
            {bvaError && <div className="mt-3"><Alert>{bvaError}</Alert></div>}
            {bva.length > 0 && (
              <table className="mt-3 w-full text-sm">
                <thead><tr className="border-b text-left text-xs font-medium text-neutral-500">
                  <th className="pb-2 pr-4">Category</th><th className="pb-2 pr-4">Limit</th><th className="pb-2 pr-4">Spent</th><th className="pb-2">%</th>
                </tr></thead>
                <tbody>{bva.map((r, i) => (
                  <tr key={i} className="border-b border-neutral-100">
                    <td className="py-2 pr-4">{catMap[r.category_id] || r.category_id}</td>
                    <td className="py-2 pr-4 tabular-nums">${Number(r.limit_amount).toFixed(2)}</td>
                    <td className="py-2 pr-4 tabular-nums">${Number(r.spent_amount).toFixed(2)}</td>
                    <td className={`py-2 tabular-nums font-medium ${Number(r.percent) >= 1 ? "text-red-600" : Number(r.percent) >= 0.8 ? "text-amber-600" : "text-emerald-600"}`}>
                      {(Number(r.percent) * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>
        </>
      )}
    </AppShell>
  );
}
