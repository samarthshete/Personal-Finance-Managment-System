"use client";
import { useEffect, useState, useCallback } from "react";
import AppShell from "@/components/AppShell";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Select from "@/components/ui/Select";
import Alert from "@/components/ui/Alert";
import Badge from "@/components/ui/Badge";
import Spinner from "@/components/ui/Spinner";
import EmptyState from "@/components/ui/EmptyState";
import PageHeader from "@/components/ui/PageHeader";
import Modal from "@/components/ui/Modal";
import { apiFetch, ApiError } from "@/lib/api";

interface Job {
  id: string;
  type: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_message: string | null;
  error_trace: string | null;
}

const statusOpts = [
  { value: "", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "running", label: "Running" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
];

const statusColors: Record<string, "green" | "red" | "yellow" | "gray"> = {
  succeeded: "green",
  failed: "red",
  running: "yellow",
  pending: "yellow",
};

function formatDuration(started: string | null, finished: string | null): string {
  if (!started || !finished) return "—";
  const a = new Date(started).getTime();
  const b = new Date(finished).getTime();
  const sec = Math.round((b - a) / 1000);
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      if (typeFilter) params.set("type", typeFilter);
      const q = params.toString();
      const data = await apiFetch<Job[]>(`/api/v1/jobs${q ? `?${q}` : ""}`);
      setJobs(data);
    } catch (e) {
      setError((e as ApiError).detail);
    }
    setLoading(false);
  }, [statusFilter, typeFilter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell>
      <PageHeader title="Jobs" />
      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}

      <Card className="mb-6">
        <div className="flex flex-wrap gap-3 items-end">
          <Select
            label="Status"
            options={statusOpts}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          />
          <Input
            label="Type"
            type="text"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            placeholder="e.g. report.generate"
          />
          <Button onClick={load}>Refresh</Button>
        </div>
      </Card>

      {loading ? (
        <Spinner />
      ) : jobs.length === 0 ? (
        <EmptyState message="No jobs yet. Jobs are created when you import transactions or generate reports." />
      ) : (
        <div className="space-y-3">
          {jobs.map((j) => (
            <Card
              key={j.id}
              className="cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => setSelectedJob(j)}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-neutral-900">{j.type}</p>
                  <p className="text-xs text-neutral-500">
                    {new Date(j.created_at).toLocaleString()} · Duration: {formatDuration(j.started_at, j.finished_at)}
                  </p>
                </div>
                <Badge color={statusColors[j.status] || "gray"}>{j.status}</Badge>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal open={!!selectedJob} onClose={() => setSelectedJob(null)} title="Job Detail">
        {selectedJob && (
          <div className="space-y-3 text-sm">
            <div>
              <p className="font-medium text-neutral-600 mb-1">Payload</p>
              <pre className="max-h-40 overflow-auto rounded border bg-neutral-50 p-2 text-xs">
                {JSON.stringify(selectedJob.payload, null, 2)}
              </pre>
            </div>
            {selectedJob.result && (
              <div>
                <p className="font-medium text-neutral-600 mb-1">Result</p>
                <pre className="max-h-40 overflow-auto rounded border bg-neutral-50 p-2 text-xs">
                  {JSON.stringify(selectedJob.result, null, 2)}
                </pre>
              </div>
            )}
            {selectedJob.error_message && (
              <div>
                <p className="font-medium text-red-600 mb-1">Error</p>
                <p className="text-red-700">{selectedJob.error_message}</p>
              </div>
            )}
            {selectedJob.error_trace && (
              <div>
                <p className="font-medium text-neutral-600 mb-1">Trace</p>
                <pre className="max-h-32 overflow-auto rounded border bg-neutral-50 p-2 text-xs text-neutral-600">
                  {selectedJob.error_trace}
                </pre>
              </div>
            )}
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
