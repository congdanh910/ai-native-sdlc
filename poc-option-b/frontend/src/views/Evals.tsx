import { useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { ActionButton, Badge, Card, Empty, ErrorBox, Field, Loading, Mono, PageHeader, Select, StatusBadge } from "../components/ui";
import { api, type EvalFingerprint, type EvalRun, type EvalSummary, type Suite } from "../lib/api";
import { cn, pct, role, when } from "../lib/utils";

const ROLES = ["business_analyst", "tech_lead", "qa_analyst", "code_reviewer"];
const ENGINES = ["qwen3", "claude-readonly", "codex-readonly"];
const METRICS: [keyof EvalSummary, string, "%" | "s" | ""][] = [
  ["grounding_pass_v1", "Grounding pass, first plan", "%"],
  ["grounding_pass_final", "Grounding pass, final plan", "%"],
  ["file_recall", "Files of the approved plan that were planned", "%"],
  ["file_precision", "Planned files the approved plan also touched", "%"],
  ["asked_blocking_questions", "Cases stopped by a blocking question", ""],
  ["mean_seconds_to_plan", "Mean seconds from request to plan", "s"],
];

function metric(value: number | null | undefined, unit: string) {
  if (value === null || value === undefined) return "–";
  return unit === "%" ? pct(value) : `${Math.round(value)}${unit}`;
}

export function engineLabel(fp: EvalFingerprint) {
  const others = Object.entries(fp.engines ?? {}).filter(([, e]) => e !== "qwen3").map(([r, e]) => `${role(r)}: ${e}`);
  return others.length ? others.join(" · ") : `${fp.team_mode} ${fp.model}`;
}

/** Grounding on the first plan across evals of one suite, oldest to newest. */
function Trend({ runs }: { runs: EvalRun[] }) {
  const points = runs.filter((e) => e.summary?.grounding_pass_v1 !== null && e.summary?.grounding_pass_v1 !== undefined).slice(-16);
  if (points.length < 2) return null;
  return (
    <div className="flex items-end gap-1.5" role="img" aria-label="First-plan grounding per eval, oldest to newest">
      {points.map((e) => {
        const v = e.summary?.grounding_pass_v1 ?? 0;
        return (
          <Link key={e.id} href={`/evals/${e.id}`} title={`${when(e.created_at)} · ${pct(v)} · ${engineLabel(e.fingerprint)}`}
            className={cn("w-5 rounded-t", Object.values(e.fingerprint.engines ?? {}).some((x) => x !== "qwen3") ? "bg-violet" : "bg-accent")}
            style={{ height: `${8 + v * 56}px` }} />
        );
      })}
    </div>
  );
}

export function EvalsView() {
  const [, navigate] = useLocation();
  const suites = useQuery({ queryKey: ["suites"], queryFn: () => api<Suite[]>("/api/eval-suites") });
  const runs = useQuery({ queryKey: ["evals"], queryFn: () => api<EvalRun[]>("/api/evals"), refetchInterval: 10_000 });
  const [engines, setEngines] = useState<Record<string, string>>(Object.fromEntries(ROLES.map((r) => [r, "qwen3"])));
  if (suites.isLoading || runs.isLoading) return <Loading />;
  if (suites.error || runs.error) return <ErrorBox error={suites.error ?? runs.error} />;
  const history = runs.data ?? [];
  return (
    <>
      <PageHeader title="Evals" subtitle="Plan-only evals run the thinking roles on fixed features at a pinned commit and compare their plans with approved plans. Runs never reach coding." />
      <Card className="flex flex-col gap-3 p-5">
        <h2 className="m-0 text-base font-semibold">Engines for the next eval</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {ROLES.map((r) => (
            <Field key={r} label={role(r)} htmlFor={`e-${r}`}>
              <Select id={`e-${r}`} value={engines[r]} onChange={(e) => setEngines({ ...engines, [r]: e.target.value })}>
                {ENGINES.map((e) => <option key={e}>{e}</option>)}
              </Select>
            </Field>
          ))}
        </div>
        <p className="m-0 text-xs text-muted">claude-readonly and codex-readonly use your subscriptions and send repository content to Anthropic or OpenAI.</p>
      </Card>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(suites.data ?? []).map((suite) => (
          <Card key={suite.name} className="flex flex-col gap-3 p-5">
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 className="m-0 text-lg font-semibold">{suite.name}</h2>
              <Badge>{suite.cases.length} cases</Badge>
              <ActionButton className="ml-auto" variant="primary" action={async () => {
                const evaluation = await api<EvalRun>("/api/evals", { method: "POST", body: { suite: suite.name, engines } });
                navigate(`/evals/${evaluation.id}`);
              }}>Run {suite.name}</ActionButton>
            </div>
            <div className="text-sm text-muted">project {suite.project} · base <Mono>{suite.base_commit.slice(0, 10)}</Mono> · {suite.cases.join(", ")}</div>
            <Trend runs={history.filter((e) => e.suite === suite.name).slice().reverse()} />
          </Card>
        ))}
      </div>
      <h2 className="m-0 text-sm font-semibold uppercase tracking-wide text-muted">History</h2>
      {history.length ? (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead><tr className="bg-side text-left text-xs text-muted">{["Started", "Suite", "Status", "Planned", "Grounded v1", "Final", "Recall", "Engines"].map((h) => <th key={h} className="border-b border-line px-3 py-2.5 font-semibold">{h}</th>)}</tr></thead>
            <tbody>
              {history.map((e) => (
                <tr key={e.id} className="hover:bg-sunken">
                  <td className="border-b border-line px-3 py-2"><Link href={`/evals/${e.id}`}>{when(e.created_at)}</Link></td>
                  <td className="border-b border-line px-3 py-2">{e.suite}</td>
                  <td className="border-b border-line px-3 py-2"><StatusBadge status={e.status} /></td>
                  <td className="border-b border-line px-3 py-2">{e.summary ? `${e.summary.planned}/${e.summary.cases}` : "–"}</td>
                  <td className="border-b border-line px-3 py-2 font-semibold">{metric(e.summary?.grounding_pass_v1, "%")}</td>
                  <td className="border-b border-line px-3 py-2">{metric(e.summary?.grounding_pass_final, "%")}</td>
                  <td className="border-b border-line px-3 py-2">{metric(e.summary?.file_recall, "%")}</td>
                  <td className="border-b border-line px-3 py-2 text-xs text-muted">{engineLabel(e.fingerprint)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : <Empty>No evals yet.</Empty>}
    </>
  );
}

export function EvalView({ id }: { id: string }) {
  const e = useQuery({ queryKey: ["eval", id], queryFn: () => api<EvalRun>(`/api/evals/${id}`),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 5000 : false) });
  if (e.isLoading) return <Loading />;
  if (e.error || !e.data) return <ErrorBox error={e.error} />;
  const ev = e.data;
  const base = ev.baseline?.summary;
  return (
    <>
      <nav aria-label="Breadcrumb" className="text-sm text-muted"><Link href="/evals">Evals</Link> / {ev.suite}</nav>
      <PageHeader title={<span className="inline-flex items-center gap-3">Eval {ev.suite}<StatusBadge status={ev.status} /></span>}
        subtitle={`${when(ev.created_at)} · ${engineLabel(ev.fingerprint)} · context ${ev.fingerprint.context_char_budget} chars${ev.baseline ? ` · baseline ${when(ev.baseline.created_at)}` : ""}`} />
      {ev.status === "running" && ev.progress ? (
        <Card className="flex flex-wrap gap-2 p-4">{ev.progress.map((p) => <Badge key={p.case} tone="neutral">{p.case} · {p.status.toLowerCase()}</Badge>)}</Card>
      ) : null}
      {ev.summary ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {METRICS.map(([key, label, unit]) => {
            const now = ev.summary?.[key] as number | null, before = base?.[key] as number | null | undefined;
            return (
              <Card key={key} className="p-4">
                <div className="text-xs text-muted">{label}</div>
                <div className="mt-1 text-2xl font-semibold tracking-tight">{metric(now, unit)}</div>
                <div className="text-xs text-muted">baseline {metric(before, unit)}</div>
              </Card>
            );
          })}
        </div>
      ) : null}
      <div className="flex flex-col gap-3">
        {(ev.cases ?? []).map((c) => (
          <Card key={c.case} className="flex flex-col gap-2 p-4">
            <div className="flex flex-wrap items-center gap-2.5">
              <b>{c.case}</b>
              {c.plans ? <StatusBadge status={c.grounding_final ?? "none"} /> : <StatusBadge status={c.status} />}
              {c.regenerated ? <Badge tone="warn">replanned</Badge> : null}
              <Link className="ml-auto text-sm" href={`/runs/${c.run_id}`}>run →</Link>
            </div>
            {c.plans ? (
              <>
                <div className="text-sm text-muted">{c.criteria} criteria · {c.tasks} tasks (approved plan {c.golden_tasks}) · {c.test_cases} test cases · {c.seconds_to_plan}s</div>
                <div className="flex flex-wrap gap-1.5">{(c.files ?? []).map((f) => <Badge key={f} tone={(c.missed_files ?? []).includes(f) ? "bad" : "neutral"}><Mono>{f}</Mono></Badge>)}
                  {(c.missed_files ?? []).map((f) => <Badge key={`m-${f}`} tone="bad">missed <Mono>{f}</Mono></Badge>)}</div>
                {(c.findings_final ?? []).length ? <ul className="m-0 pl-5 text-sm text-bad">{c.findings_final!.map((f) => <li key={f}>{f}</li>)}</ul> : null}
                {c.usage ? <div className="text-xs text-muted">{Object.entries(c.usage).map(([eng, u]) => `${eng}: ${u.calls} call(s), ${Math.round(u.seconds)}s${u.cost_usd ? `, $${u.cost_usd}` : ""}`).join(" · ")}</div> : null}
              </>
            ) : <div className="text-sm text-muted">{c.reason}{c.blocking_questions.length ? ` · asked: ${c.blocking_questions.join(" | ")}` : ""}</div>}
          </Card>
        ))}
      </div>
    </>
  );
}
