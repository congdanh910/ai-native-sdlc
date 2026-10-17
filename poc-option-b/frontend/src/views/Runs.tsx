import { useState } from "react";
import { Link } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Card, Empty, ErrorBox, Loading, Mono, PageHeader, Select, StatusBadge } from "../components/ui";
import { api, type Project, type RunSummary } from "../lib/api";
import { ago, cn, role } from "../lib/utils";

const ALL = "";

/** Open the Runs page filtered to one project (the breadcrumb of a run links here). */
export function rememberProject(project: string): void {
  try { localStorage.setItem("runsProject", project); } catch { /* private mode */ }
}

function remembered(): string {
  try { return localStorage.getItem("runsProject") ?? ALL; } catch { return ALL; }
}

export function RunsView() {
  const [project, setProject] = useState(remembered);
  const [showExperiments, setShowExperiments] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
  // The API filters by project, so a busy project does not crowd out the others (it returns at most 200 runs).
  const runs = useQuery({
    queryKey: ["runs", project],
    queryFn: () => api<RunSummary[]>(project ? `/api/runs?project=${encodeURIComponent(project)}` : "/api/runs"),
    refetchInterval: 10_000,
  });
  const choose = (value: string) => {
    setProject(value);
    try { localStorage.setItem("runsProject", value); } catch { /* private mode */ }
  };
  if (runs.isLoading) return <Loading />;
  if (runs.error) return <ErrorBox error={runs.error} />;
  const known = (projects.data ?? []).map((p) => p.key);
  const list = (runs.data ?? []).filter((r) => showExperiments || !r.experiment);
  return (
    <>
      <PageHeader
        title="Runs"
        subtitle={`${list.length} run(s)${project ? ` in ${project}` : ""}, newest first.`}
        actions={
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-muted" htmlFor="runs-project">
              Project
              <Select id="runs-project" className="h-9 w-48" value={known.includes(project) ? project : ALL} onChange={(e) => choose(e.target.value)}>
                <option value={ALL}>All projects</option>
                {known.map((key) => <option key={key} value={key}>{key}</option>)}
              </Select>
            </label>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={showExperiments} onChange={(e) => setShowExperiments(e.target.checked)} />
              Show eval experiments
            </label>
          </div>
        }
      />
      {list.length === 0 ? (
        <Empty>{project ? <>No runs in {project} yet. <Link href="/new">Start a request</Link>.</> : "No runs yet."}</Empty>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead>
              <tr className="bg-side text-left text-xs text-muted">
                {["Run", ...(project ? [] : ["Project"]), "Status", "Waiting for", "Updated"].map((h) => (
                  <th key={h} className="border-b border-line px-4 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {list.map((run) => (
                <tr key={run.id} className={cn("hover:bg-sunken", run.experiment && "opacity-70")}>
                  <td className="border-b border-line px-4 py-3">
                    <Link href={`/runs/${run.id}`} className="font-semibold text-ink hover:text-accent">{run.title}</Link>
                    <div><Mono className="text-muted">{run.id.slice(0, 8)}{run.experiment ? " · experiment" : ""}</Mono></div>
                  </td>
                  {project ? null : (
                    <td className="border-b border-line px-4 py-3">
                      <button type="button" className="text-ink hover:text-accent" onClick={() => choose(run.project)} title={`Show only ${run.project}`}>
                        {run.project}
                      </button>
                    </td>
                  )}
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
