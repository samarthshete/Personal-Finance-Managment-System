"use client";
import { useEffect, useState, useCallback } from "react";
import AppShell from "@/components/AppShell";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Alert from "@/components/ui/Alert";
import Spinner from "@/components/ui/Spinner";
import PageHeader from "@/components/ui/PageHeader";
import { apiFetch, ApiError } from "@/lib/api";

interface UserProfile {
  id: string;
  name: string;
  email: string;
  preferred_currency: string;
  monthly_income_goal: number | null;
  display_title: string | null;
}

export default function ProfilePage() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  // Form state
  const [name, setName] = useState("");
  const [preferredCurrency, setPreferredCurrency] = useState("USD");
  const [monthlyIncomeGoal, setMonthlyIncomeGoal] = useState("");
  const [displayTitle, setDisplayTitle] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p = await apiFetch<UserProfile>("/api/v1/me");
      setProfile(p);
      setName(p.name ?? "");
      setPreferredCurrency(p.preferred_currency ?? "USD");
      setMonthlyIncomeGoal(p.monthly_income_goal != null ? String(p.monthly_income_goal) : "");
      setDisplayTitle(p.display_title ?? "");
    } catch (e) {
      setError((e as ApiError).detail || "Failed to load profile");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setSuccess(false);

    const payload: Record<string, string | number | null> = {};
    if (name.trim()) payload.name = name.trim();
    if (preferredCurrency.trim()) payload.preferred_currency = preferredCurrency.trim().toUpperCase();
    payload.display_title = displayTitle.trim() || null;
    if (monthlyIncomeGoal.trim() !== "") {
      const val = Number(monthlyIncomeGoal);
      if (isNaN(val) || val < 0) {
        setError("Monthly income goal must be a positive number.");
        setSaving(false);
        return;
      }
      payload.monthly_income_goal = val;
    } else {
      payload.monthly_income_goal = null;
    }

    try {
      const updated = await apiFetch<UserProfile>("/api/v1/me", {
        method: "PATCH",
        body: payload,
      });
      setProfile(updated);
      setName(updated.name);
      setPreferredCurrency(updated.preferred_currency);
      setMonthlyIncomeGoal(updated.monthly_income_goal != null ? String(updated.monthly_income_goal) : "");
      setDisplayTitle(updated.display_title ?? "");
      setSuccess(true);
    } catch (e) {
      const err = e as ApiError;
      setError(err.detail || "Failed to save profile");
    }
    setSaving(false);
  }

  if (loading) {
    return (
      <AppShell>
        <PageHeader title="Profile" />
        <Spinner />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader title="Profile" />

      {/* Identity summary */}
      {profile && (
        <div className="mb-6 flex items-center gap-4">
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-neutral-900 text-lg font-bold text-white select-none">
            {profile.name
              .trim()
              .split(/\s+/)
              .slice(0, 2)
              .map((w) => w[0]?.toUpperCase() ?? "")
              .join("")}
          </span>
          <div>
            <p className="text-lg font-semibold text-neutral-900">{profile.name}</p>
            <p className="text-sm text-neutral-500">{profile.email}</p>
            {profile.display_title && (
              <p className="text-xs text-neutral-400 mt-0.5">{profile.display_title}</p>
            )}
          </div>
        </div>
      )}

      <Card className="max-w-lg">
        <form onSubmit={handleSave} className="space-y-5">
          {error && <Alert>{error}</Alert>}
          {success && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
              Profile saved successfully.
            </div>
          )}

          <Input
            label="Full Name"
            value={name}
            onChange={(e) => { setName(e.target.value); setSuccess(false); }}
            placeholder="Your name"
            required
          />

          <div className="space-y-1">
            <label className="block text-sm font-medium text-neutral-700">Email</label>
            <input
              value={profile?.email ?? ""}
              readOnly
              disabled
              className="w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-400 cursor-not-allowed"
            />
            <p className="text-xs text-neutral-400">Email cannot be changed here.</p>
          </div>

          <Input
            label="Display Title"
            value={displayTitle}
            onChange={(e) => { setDisplayTitle(e.target.value); setSuccess(false); }}
            placeholder="e.g. Senior Engineer, Freelancer"
          />

          <Input
            label="Preferred Currency"
            value={preferredCurrency}
            onChange={(e) => { setPreferredCurrency(e.target.value.toUpperCase()); setSuccess(false); }}
            placeholder="USD"
            maxLength={10}
          />

          <Input
            label="Monthly Income Goal"
            type="number"
            min="0"
            step="0.01"
            value={monthlyIncomeGoal}
            onChange={(e) => { setMonthlyIncomeGoal(e.target.value); setSuccess(false); }}
            placeholder="e.g. 5000"
          />

          <div className="flex justify-end pt-1">
            <Button type="submit" loading={saving}>
              Save Changes
            </Button>
          </div>
        </form>
      </Card>
    </AppShell>
  );
}
