import { useState } from "react";
import { Link } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Card, Empty, ErrorBox, Loading, Mono, PageHeader, StatusBadge } from "../components/ui";
import { api, type RunSummary } from "../lib/api";
import { ago, cn, role } from "../lib/utils";

export function RunsView() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/api/runs"), refetchInterval: 10_000 });
  const [showExperiments, setShowExperiments] = useState(false);
  if (runs.isLoading) return <Loading />;
  if (runs.error) return <ErrorBox error={runs.error} />;
  const list = (runs.data ?? []).filter((r) => showExperiments || !r.experiment);
  return (
    <>
      <PageHeader
        title="Runs"
        subtitle={`${list.length} run(s), newest first.`}
        actions={
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={showExperiments} onChange={(e) => setShowExperiments(e.target.checked)} />
            Show eval experiments
          </label>
        }
      />
      {list.length === 0 ? <Empty>No runs yet.</Empty> : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead>
              <tr className="bg-side text-left text-xs text-muted">
                {["Run", "Project", "Status", "Waiting for", "Updated"].map((h) => <th key={h} className="border-b border-line px-4 py-2.5 font-semibold">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {list.map((run) => (
                <tr key={run.id} className={cn("hover:bg-sunken", run.experiment && "opacity-70")}>
                  <td className="border-b border-line px-4 py-3">
                    <Link href={`/runs/${run.id}`} className="font-semibold text-ink hover:text-accent">{run.title}</Link>
                    <div><Mono className="text-muted">{run.id.slice(0, 8)}{run.experiment ? " · experiment" : ""}</Mono></div>
                  </td>
                  <td className="border-b border-line px-4 py-3">{run.project}</td>
                  <td className="border-b border-line px-4 py-3"><StatusBadge status={run.status} human /></td>
                  <td className="border-b border-line px-4 py-3">{run.waiting ? role(run.waiting.role) : ""}</td>
                  <td className="border-b border-line px-4 py-3 text-muted">{ago(run.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
