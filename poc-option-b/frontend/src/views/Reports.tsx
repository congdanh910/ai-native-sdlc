import { useQuery } from "@tanstack/react-query";
import { Card, ErrorBox, Loading, Mono, PageHeader, Stat } from "../components/ui";
import { api, type Report } from "../lib/api";
import { pct, role, when } from "../lib/utils";
import { Link } from "wouter";

const ROLE_ORDER = ["business_analyst", "tech_lead", "qa_analyst", "qa_engineer", "developer", "validation_gate", "code_reviewer", "release_engineer"];

function label(key: string) {
  const s = key.replace(/_/g, " ").replace(/\bprs\b/g, "PRs").replace(/\bci\b/g, "CI");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function ReportsView() {
  const report = useQuery({ queryKey: ["reports"], queryFn: () => api<Report>("/api/reports"), refetchInterval: 30_000 });
  if (report.isLoading) return <Loading />;
  if (report.error || !report.data) return <ErrorBox error={report.error} />;
  const r = report.data;
  const maxSeconds = Math.max(1, ...Object.values(r.engines).map((u) => u.seconds));
  return (
    <>
      <PageHeader title="Team report" subtitle={`Normal runs only (${r.runs.total}); engines are compared on evals below.`} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Runs" value={r.runs.total} sub={Object.entries(r.runs.by_project).map(([k, v]) => `${k} ${v}`).join(" · ")} />
        <Stat label="Completed" value={r.runs.by_status.COMPLETED ?? 0} />
        <Stat label="Corrected by a human" value={r.runs.human_corrected} />
        <Stat label="Needs a human now" value={r.runs.by_status.NEEDS_HUMAN ?? 0} />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {ROLE_ORDER.filter((k) => r.roles[k]).map((k) => (
          <Card key={k} className="flex flex-col gap-2 p-4">
            <h2 className="m-0 text-base font-semibold">{role(k)}</h2>
            <dl className="m-0 flex flex-col gap-1.5 text-sm">
              {Object.entries(r.roles[k]).map(([m, v]) => (
                <div key={m} className="flex justify-between gap-3">
                  <dt className="text-muted">{label(m)}</dt>
                  <dd className="m-0 font-semibold">{v === null ? "–" : m.includes("grounded") ? pct(v) : v}</dd>
                </div>
              ))}
            </dl>
          </Card>
        ))}
      </div>
      <Card className="flex flex-col gap-3 p-5">
        <h2 className="m-0 text-base font-semibold">Engines on normal runs</h2>
        {Object.entries(r.engines).sort((a, b) => b[1].seconds - a[1].seconds).map(([engine, u]) => (
          <div key={engine} className="grid grid-cols-[180px_1fr_auto] items-center gap-3 text-sm">
            <Mono>{engine}</Mono>
            <div className="h-2.5 rounded-full bg-sunken"><div className="h-2.5 rounded-full bg-accent" style={{ width: `${(u.seconds / maxSeconds) * 100}%` }} /></div>
            <span className="text-muted">{u.calls} call(s) · {Math.round(u.seconds)}s{u.cost_usd ? ` · ≈$${u.cost_usd} at API prices (subscription, not billed)` : ""}</span>
          </div>
        ))}
      </Card>
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead><tr className="bg-side text-left text-xs text-muted">{["Eval", "Suite", "Engines", "Planned", "Grounded v1", "Recall", "Blocking"].map((h) => <th key={h} className="border-b border-line px-3 py-2.5 font-semibold">{h}</th>)}</tr></thead>
          <tbody>
            {r.evals.slice().reverse().map((e) => (
              <tr key={e.id}>
                <td className="border-b border-line px-3 py-2"><Link href={`/evals/${e.id}`}>{when(e.created_at)}</Link></td>
                <td className="border-b border-line px-3 py-2">{e.suite}</td>
                <td className="border-b border-line px-3 py-2 text-xs text-muted">{Object.entries(e.engines ?? {}).filter(([, v]) => v !== "qwen3").map(([k, v]) => `${role(k)}: ${v}`).join(" · ") || "qwen3"}</td>
                <td className="border-b border-line px-3 py-2">{e.summary ? `${e.summary.planned}/${e.summary.cases}` : "–"}</td>
                <td className="border-b border-line px-3 py-2 font-semibold">{pct(e.summary?.grounding_pass_v1)}</td>
                <td className="border-b border-line px-3 py-2">{pct(e.summary?.file_recall)}</td>
                <td className="border-b border-line px-3 py-2">{e.summary?.asked_blocking_questions ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
