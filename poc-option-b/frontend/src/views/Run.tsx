import { useState } from "react";
import { AcceptanceReport } from "../components/AcceptanceReport";
import { Link } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, FileCode2, GitCommit, X } from "lucide-react";
import { api, projectDoc, type Doc, type PlanVersion, type RunDetail, type RunEvent, type Step } from "../lib/api";
import { ACTIVE_WORK, TERMINAL, ago, cn, duration, role, short, when, parseCriteria } from "../lib/utils";
import { Pipeline } from "../components/Pipeline";
import { PreviewBox } from "../components/PreviewBox";
import { DecisionPanel } from "../components/Decision";
import { CoverageMatrix, GroundingSummary } from "../components/Coverage";
import { JobLog } from "../components/JobLog";
import { JobPrompts } from "../components/Prompts";
import { DecisionsTab, QuestionsTab } from "../components/Questions";
import { rememberProject } from "./Runs";
import { RequirementsTab } from "../components/Requirements";
import { SourcesTab } from "../components/Sources";
import { PlanSummary } from "../components/PlanSummary";
import { ActionButton, Badge, Button, Card, Empty, ErrorBox, Loading, Markdown, Mono, PageHeader, Select, StatusBadge, Tabs } from "../components/ui";

type Tab = "sources" | "requirements" | "questions" | "decisions" | "tasks" | "plan" | "acceptance" | "thread" | "diff" | "events" | "work";

const STEP_LABEL: Record<string, string> = {
  WRITING_TESTS: "QA writes tests", RED_CHECK: "Red check", CODING: "Developer", REMEDIATING: "Developer fixes", VALIDATING: "Validation",
};

function stepOutcome(step: Step): { text: string; tone: "good" | "warn" | "bad" | "neutral" } {
  if (step.dispute) return { text: "dispute", tone: "bad" };
  if (step.state === "RED_CHECK") return step.ok ? { text: "red as expected", tone: "warn" } : { text: "not red", tone: "bad" };
  if (step.state === "VALIDATING") return step.ok ? { text: "green", tone: "good" } : { text: "failing", tone: "bad" };
  if (step.detail.includes("reused") || step.detail.includes("passed before")) return { text: "reused", tone: "neutral" };
  return step.ok ? { text: "done", tone: "good" } : { text: "rejected", tone: "bad" };
}

function StepTile({ step }: { step: Step }) {
  const outcome = stepOutcome(step);
  return (
    <div className={cn("flex min-w-0 flex-[1_1_150px] flex-col gap-1 rounded-xl border px-3 py-2.5",
      outcome.tone === "bad" ? "border-bad/40 bg-bad-soft/60" : "border-line bg-panel")}>
      <div className="text-xs text-muted">{STEP_LABEL[step.state] ?? step.state}{step.executor ? ` · ${step.executor}` : step.state.match(/RED|VALID/) ? " · docker" : ""}</div>
      <div className={cn("font-semibold", outcome.tone === "good" && "text-good", outcome.tone === "warn" && "text-warn", outcome.tone === "bad" && "text-bad")}>
        {outcome.text}{step.seconds ? <span className="font-normal text-muted"> · {duration(step.seconds)}</span> : null}
      </div>
      {step.commit ? <div className="flex items-center gap-1 text-xs"><GitCommit className="size-3 text-muted" aria-hidden /><Mono>{short(step.commit)}</Mono></div> : null}
    </div>
  );
}

function TasksTab({ run }: { run: RunDetail }) {
  if (!run.tasks.length) return <Empty>Tasks appear after the plan is approved.</Empty>;
  const plan = run.plans[run.plans.length - 1];
  return (
    <div className="flex flex-col gap-3">
      {run.tasks.map((task) => {
        const attempts = run.task_steps.filter((x) => x.task === task.id);
        const planned = plan?.plan.tasks.find((t) => t.id === task.id);
        const files = planned ? [...planned.modify, ...planned.create] : [];
        const total = attempts.flatMap((a) => a.steps).reduce((s, x) => s + (x.seconds ?? 0), 0);
        return (
          <Card key={task.id} className={cn("flex flex-col gap-3 p-4", ["FAILED", "DISPUTED", "UNKNOWN"].includes(task.status) && "border-bad/40")}>
            <div className="flex flex-wrap items-center gap-2.5">
              <Mono className="font-semibold">{task.id}</Mono>
              <b className="min-w-0">{task.title}</b>
              {files.map((f) => <Mono key={f} className="text-muted">{f}</Mono>)}
              <span className="ml-auto flex items-center gap-2">
                {total ? <span className="text-xs text-muted">{duration(total)}</span> : null}
                <StatusBadge status={task.status} />
              </span>
            </div>
            {attempts.length === 0 ? <p className="m-0 text-sm text-muted">{task.status === "PENDING" ? `Waits for ${task.depends_on.join(", ") || "its turn"}.` : "Starting…"}</p> : null}
            {attempts.map((attempt, i) => (
              <div key={i} className="flex flex-col gap-2">
                {attempts.length > 1 ? <div className="text-xs font-semibold text-muted">Attempt {i + 1}</div> : null}
                <div className="flex flex-wrap gap-2">{attempt.steps.map((s, j) => <StepTile key={j} step={s} />)}</div>
                {attempt.steps.filter((s) => !s.ok || s.dispute).map((s, j) => (
                  <details key={j} open={i === attempts.length - 1} className="rounded-xl bg-code-bg text-code-ink">
                    <summary className="cursor-pointer px-3.5 py-2.5 text-sm font-semibold">{STEP_LABEL[s.state] ?? s.state}: why</summary>
                    <pre className="m-0 whitespace-pre-wrap px-3.5 pb-3.5 font-mono text-xs leading-relaxed">{s.detail}</pre>
                  </details>
                ))}
              </div>
            ))}
          </Card>
        );
      })}
    </div>
  );
}

function VersionPicker<T extends { version: number; author: string; author_kind: string }>({ items, children }: { items: T[]; children: (item: T) => React.ReactNode }) {
  // null = follow the latest version: a document that appears while the page is open shows at once
  const [picked, setPicked] = useState<number | null>(null);
  if (!items.length) return <p className="m-0 text-muted">Not produced yet.</p>;
  const index = picked === null ? items.length - 1 : Math.min(picked, items.length - 1);
  const item = items[index];
  return (
    <div className="flex flex-col gap-3">
      {items.length > 1 ? (
        <Select aria-label="Version" className="max-w-xs" value={index} onChange={(e) => setPicked(Number(e.target.value))}>
          {items.map((it, i) => <option key={i} value={i}>v{it.version} by {it.author}{it.author_kind === "human" ? " (human)" : ""}</option>)}
        </Select>
      ) : null}
      {children(item)}
    </div>
  );
}

function PlanBody({ plan, spec }: { plan: PlanVersion; spec?: Doc }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>v{plan.version} by {role(plan.author)}</span><Mono>sha {short(plan.sha256, 12)}</Mono>
        {plan.commit_sha ? <span>committed <Mono>{short(plan.commit_sha)}</Mono></span> : null}
      </div>
      <GroundingSummary plan={plan} />
      <PlanSummary summary={plan.plan.summary} titles={Object.fromEntries(plan.plan.tasks.map((t) => [t.id, t.title]))} />
      <div className="overflow-hidden rounded-lg border border-line"><CoverageMatrix plan={plan} specMarkdown={spec?.content} /></div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {plan.plan.tasks.map((t) => (
          <Card key={t.id} className="flex flex-col gap-2 p-4">
            <div className="flex flex-wrap items-center gap-2"><Mono className="font-semibold">{t.id}</Mono>{[...t.modify, ...t.create].map((f) => <Mono key={f} className="text-muted">{f}</Mono>)}</div>
            <b>{t.title}</b>
            <div className="text-xs text-muted">
              {t.depends_on.length ? `Depends on ${t.depends_on.join(", ")}` : "No dependencies"}
              {t.provides.length ? <> · provides <Mono>{t.provides.join(", ")}</Mono></> : null}
            </div>
          </Card>
        ))}
      </div>
      <details className="rounded-xl border border-line">
        <summary className="cursor-pointer px-4 py-3 font-semibold">Test plan · {plan.test_plan.cases.length} cases</summary>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <tbody>
              {plan.test_plan.cases.map((c) => (
                <tr key={c.id} className={c.expected_pass ? "bg-flag-soft/60" : undefined}>
                  <td className="border-t border-line px-3 py-2 align-top"><Mono>{c.id}</Mono></td>
                  <td className="border-t border-line px-3 py-2 align-top"><Mono className="text-muted">{c.task_id} · {c.criterion}</Mono></td>
                  <td className="border-t border-line px-3 py-2 align-top text-ink-2">{c.description}{c.expected_pass ? <Badge tone="flag" className="ml-2">expected pass</Badge> : null}</td>
                  <td className="border-t border-line px-3 py-2 align-top"><Mono className="text-muted">{c.target_file}</Mono></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function DocBody({ doc }: { doc: Doc }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>v{doc.version} by {role(doc.author)}</span><Mono>sha {short(doc.sha256, 12)}</Mono>
        {doc.repo_path ? <Mono>{doc.repo_path}</Mono> : null}
        <span>{doc.commit_sha ? <>committed <Mono>{short(doc.commit_sha)}</Mono></> : "not committed"}</span>
      </div>
      <Markdown source={doc.content} />
    </div>
  );
}

function ThreadTab({ run }: { run: RunDetail }) {
  const doc = projectDoc(run);
  const kinds = doc ? ["intent", doc.kind, "report"] : run.kind === "discovery" ? ["intent", "inventory", "questions", "decisions", "baseline", "report"]
    : ["intent", "spec", "tests", "analysis", "review", "report"];
  const name = (kind: string) => (doc && kind === doc.kind ? doc.name : (doc || run.kind === "discovery") && kind === "intent" ? "brief.md" : kind === "tests" ? "test_cases.md" : `${kind}.md`);
  // A finished run opens with every document folded to its title; an active one with them open.
  const [closed, setClosed] = useState<Set<string>>(() => new Set(TERMINAL.includes(run.status) ? kinds : []));
  const toggle = (kind: string) => setClosed((now) => {
    const next = new Set(now);
    if (next.has(kind)) next.delete(kind); else next.add(kind);
    return next;
  });
  const present = kinds.filter((k) => run.documents.some((d) => d.kind === k));
  const allClosed = present.every((k) => closed.has(k));
  return (
    <div className="flex flex-col gap-3">
      {present.length > 1 ? (
        <div className="flex justify-end">
          <Button className="h-8 px-2 text-xs" onClick={() => setClosed(new Set(allClosed ? [] : kinds))}>
            {allClosed ? <ChevronsUpDown className="size-3.5" aria-hidden /> : <ChevronsDownUp className="size-3.5" aria-hidden />}{allClosed ? "Expand all" : "Collapse all"}
          </Button>
        </div>
      ) : null}
      {kinds.map((kind) => {
        const versions = run.documents.filter((d) => d.kind === kind);
        const latest = versions[versions.length - 1];
        const isClosed = closed.has(kind) && versions.length > 0;
        return (
          <Card key={kind} className="p-5">
            <h3 className={cn("mt-0 flex items-center gap-2 text-base font-semibold", isClosed ? "mb-0" : "mb-3")}>
              <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" aria-expanded={!isClosed} disabled={!versions.length}
                onClick={() => toggle(kind)}>
                {versions.length ? (isClosed ? <ChevronRight className="size-4 text-muted" aria-hidden /> : <ChevronDown className="size-4 text-muted" aria-hidden />) : null}
                <FileCode2 className="size-4 text-muted" aria-hidden />{name(kind)}
                {latest ? <span className="text-xs font-normal text-muted">v{latest.version}{versions.length > 1 ? ` · ${versions.length} versions` : ""} · {latest.content.split("\n").length} lines</span> : null}
              </button>
            </h3>
            {isClosed ? null : <VersionPicker items={versions}>{(d) => <DocBody doc={d} />}</VersionPicker>}
          </Card>
        );
      })}
    </div>
  );
}

function DiffTab({ run }: { run: RunDetail }) {
  const diff = useQuery({ queryKey: ["diff", run.id, run.version], queryFn: () => api<{ diff: string; files: string[] }>(`/api/runs/${run.id}/diff`) });
  if (diff.isLoading) return <Loading />;
  if (diff.error) return <ErrorBox error={diff.error} />;
  if (!diff.data?.diff) return <Empty>No code changes yet.</Empty>;
  const lines = diff.data.diff.split("\n");
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap gap-2 border-b border-line bg-side px-4 py-2.5 text-xs text-muted">
        {diff.data.files.length} file(s): {diff.data.files.map((f) => <Mono key={f}>{f}</Mono>)}
      </div>
      <pre className="m-0 max-h-[70vh] overflow-auto font-mono text-[12px] leading-[1.55]">
        {lines.map((line, i) => (
          <div key={i} className={cn("px-4",
            line.startsWith("+") && !line.startsWith("+++") && "bg-good-soft text-good",
            line.startsWith("-") && !line.startsWith("---") && "bg-bad-soft text-bad",
            line.startsWith("@@") && "bg-accent-soft text-accent-ink",
            line.startsWith("diff --git") && "mt-2 border-t border-line bg-sunken pt-1 font-semibold text-ink")}>{line || " "}</div>
        ))}
      </pre>
    </Card>
  );
}

function EventsTab({ run }: { run: RunDetail }) {
  const events = useQuery({ queryKey: ["events", run.id, run.version], queryFn: () => api<RunEvent[]>(`/api/runs/${run.id}/events`) });
  if (events.isLoading) return <Loading />;
  return (
    <Card className="p-2">
      <ol className="m-0 list-none p-0">
        {(events.data ?? []).slice().reverse().map((e) => (
          <li key={e.id} className="flex flex-wrap gap-x-3 gap-y-1 rounded-lg px-3 py-2 hover:bg-sunken">
            <Mono className="font-semibold">{e.type}</Mono>
            <span className="text-sm text-muted">{e.actor}{e.role ? ` (${role(e.role)})` : ""} · {when(e.created_at)}</span>
            {Object.keys(e.payload).length ? <div className="w-full truncate font-mono text-xs text-muted">{JSON.stringify(e.payload)}</div> : null}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function elapsed(start: string | null, end: string | null): string {
  if (!start) return "";
  const seconds = Math.max(0, Math.round(((end ? Date.parse(end) : Date.now()) - Date.parse(start)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function WorkTab({ run }: { run: RunDetail }) {
  // The running job's log opens by itself; any other job's log opens from its row.
  const running = [...run.executions].reverse().find((e) => e.status === "running");
  const [chosen, setChosen] = useState<string | null>(null);
  const [closed, setClosed] = useState(false);
  const open = run.executions.find((e) => e.id === (chosen ?? (closed ? null : running?.id)));
  const [promptOf, setPromptOf] = useState<string | null>(null);
  const prompted = run.executions.find((e) => e.id === promptOf);
  return (
    <div className="flex flex-col gap-3">
      {prompted ? <JobPrompts key={prompted.id} runId={run.id} executionId={prompted.id}
        title={`${prompted.kind}${prompted.task ? ` ${prompted.task}` : ""} · ${role(prompted.role)}`} onClose={() => setPromptOf(null)} /> : null}
      {open ? <JobLog key={open.id} runId={run.id} executionId={open.id}
        title={`${open.kind}${open.task ? ` ${open.task}` : ""} · ${role(open.role)}`}
        onClose={() => { setChosen(null); setClosed(true); }} /> : null}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead><tr className="bg-side text-left text-xs text-muted">{["Job", "Role", "Engine", "Status", "Time", "Attempts", "Error", ""].map((h, i) => <th key={i} className="border-b border-line px-3 py-2.5 font-semibold">{h}</th>)}</tr></thead>
          <tbody>
            {[...run.executions].reverse().map((e) => (  /* latest first */
              <tr key={e.id} className={cn(open?.id === e.id && "bg-accent-soft")}>
                <td className="border-b border-line px-3 py-2"><Mono>{e.kind}{e.task ? ` ${e.task}` : ""}</Mono></td>
                <td className="border-b border-line px-3 py-2">{role(e.role)}</td>
                <td className="border-b border-line px-3 py-2"><Mono className="text-muted">{e.engine}</Mono></td>
                <td className="border-b border-line px-3 py-2"><StatusBadge status={e.status} /></td>
                <td className="border-b border-line px-3 py-2 font-mono text-xs text-muted">{elapsed(e.started_at, e.finished_at)}</td>
                <td className="border-b border-line px-3 py-2">{e.attempts}</td>
                <td className="border-b border-line px-3 py-2 text-xs text-bad">{e.error}</td>
                <td className="border-b border-line px-3 py-2 text-right">
                  {e.engine ? <button type="button" className="mr-3 text-xs font-semibold text-accent hover:underline" onClick={() => setPromptOf(e.id)}>Prompt</button> : null}
                  <button type="button" className="text-xs font-semibold text-accent hover:underline" onClick={() => { setChosen(e.id); setClosed(false); }}>Log</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card className="p-4">
        <h3 className="mt-0 mb-2 text-base font-semibold">Human decisions</h3>
        {run.approvals.length ? (
          <ol className="m-0 list-none p-0">
            {[...run.approvals].reverse().map((a, i) => (  /* latest first */
              <li key={i} className="border-t border-line py-2 first:border-t-0">
                <b>{a.stage} {a.decision}</b> <span className="text-muted">by {a.actor} as {role(a.role)} · {when(a.created_at)}</span>
                {a.reason ? <div className="text-sm text-ink-2">{a.reason}</div> : null}
                {a.checklist?.gate ? (
                  <div className="text-xs text-muted">
                    Checklist: {(a.checklist.controller ?? []).filter((c) => c.ok).length}/{(a.checklist.controller ?? []).length} controller checks passed ·{" "}
                    {(a.checklist.human ?? []).filter((h) => h.checked).length}/{(a.checklist.human ?? []).length} review items ticked
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        ) : <p className="m-0 text-muted">No human decisions yet.</p>}
      </Card>
    </div>
  );
}

function SidePanel({ run }: { run: RunDetail }) {
  const pr = run.pull_request;
  const spec = [...run.documents].reverse().find((d) => d.kind === "spec");
  const plan = run.plans[run.plans.length - 1];
  const engines = [...new Set(run.executions.map((e) => e.engine).filter(Boolean))] as string[];
  return (
    <aside className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
      {pr ? (
        <Card className="flex flex-col gap-2.5 p-4">
          <h2 className="m-0 text-base font-semibold">Pull request</h2>
          <div className="flex justify-between gap-2"><span className="text-muted">{pr.simulated ? "Simulated" : "Draft PR"}</span>{pr.url ? <a href={pr.url} target="_blank" rel="noopener">#{pr.number}</a> : <Mono>{run.branch}</Mono>}</div>
          <div className="flex justify-between gap-2"><span className="text-muted">CI</span><StatusBadge status={pr.ci_status} /></div>
          <div className="flex justify-between gap-2"><span className="text-muted">Review</span><StatusBadge status={pr.review_status} /></div>
          <div className="flex justify-between gap-2"><span className="text-muted">Head</span><Mono>{short(pr.head_sha)}</Mono></div>
          {pr.merged_sha ? <div className="flex justify-between gap-2"><span className="text-muted">Merged</span><Mono>{short(pr.merged_sha)}</Mono></div> : null}
        </Card>
      ) : null}
      {plan && spec ? (
        <Card className="flex flex-col gap-2.5 p-4">
          <h2 className="m-0 text-base font-semibold">Criteria coverage</h2>
          <div className="grid grid-cols-4 gap-1.5">
            {parseCriteria(spec.content).map(({ id }) => {
              const n = plan.test_plan.cases.filter((c) => c.criterion === id).length;
              return (
                <div key={id} className={cn("rounded-lg px-1 py-1.5 text-center", n ? "bg-good-soft" : "bg-bad-soft")}>
                  <div className="font-mono text-[11px] text-muted">{id}</div><b className={n ? "text-good" : "text-bad"}>{n}</b>
                </div>
              );
            })}
          </div>
          <p className="m-0 text-xs text-muted">Test cases that prove each criterion.</p>
        </Card>
      ) : null}
      <Card className="flex flex-col gap-2 p-4 text-sm">
        <h2 className="m-0 text-base font-semibold">Run</h2>
        <div className="flex justify-between gap-2"><span className="text-muted">Base</span><Mono>{short(run.base_commit)}</Mono></div>
        <div className="flex justify-between gap-2"><span className="text-muted">Branch</span><Mono className="truncate">{run.branch}</Mono></div>
        <div className="flex justify-between gap-2"><span className="text-muted">Engines</span><span className="text-right font-mono text-xs">{engines.join(", ")}</span></div>
        <div className="flex justify-between gap-2"><span className="text-muted">Modes</span><span className="text-right text-xs">{Object.entries(run.stage_modes).map(([k, v]) => `${k} ${v}`).join(" · ")}</span></div>
        <div className="flex justify-between gap-2"><span className="text-muted">Created</span><span>{when(run.created_at)}</span></div>
      </Card>
    </aside>
  );
}

export function RunView({ id }: { id: string }) {
  const client = useQueryClient();
  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => api<RunDetail>(`/api/runs/${id}`),
    refetchInterval: (q) => {
      const data = q.state.data;
      return data && ACTIVE_WORK.includes(data.status) && !data.waiting ? 2000 : 10_000;
    },
  });
  const [tab, setTab] = useState<Tab>(() => (sessionStorage.getItem("runTab") as Tab) || "tasks");
  const [picked, setPicked] = useState(false);  // the operator chose a tab on this page
  if (run.isLoading) return <Loading />;
  if (run.error || !run.data) return <ErrorBox error={run.error} />;
  const r = run.data;
  const refresh = () => { client.invalidateQueries({ queryKey: ["run", id] }); client.invalidateQueries({ queryKey: ["inbox"] }); };
  const select = (t: Tab) => { setTab(t); setPicked(true); try { sessionStorage.setItem("runTab", t); } catch { /* ignore */ } };
  const spec = [...r.documents].reverse().find((d) => d.kind === "spec");
  const srs = r.kind !== "feature";  // an SRS, SDS or discovery run has no tasks, plan or code
  const discovery = r.kind === "discovery";
  // A discovery waiting for its sources opens on the Sources tab, whatever tab was open before.
  const ingesting = discovery && r.status === "INGESTING" && !(r.sources?.length) && !picked;
  const shown: Tab = ingesting ? "sources" : discovery && ["tasks", "plan", "diff"].includes(tab) ? "sources"
    : srs && !discovery && ["tasks", "plan", "diff", "sources", "requirements", "questions", "decisions"].includes(tab) ? "thread"
    : !discovery && ["sources", "requirements", "questions", "decisions"].includes(tab) ? "tasks" : tab;
  return (
    <>
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/runs" onClick={() => rememberProject("")}>Runs</Link> / <Link href="/runs" onClick={() => rememberProject(r.project)} title={`Runs of ${r.project}`}>{r.project}</Link> / <Mono>{r.id.slice(0, 8)}</Mono>
      </nav>
      <PageHeader
        title={<span className="inline-flex flex-wrap items-center gap-3">{r.title}<StatusBadge status={r.status} human /></span>}
        subtitle={<>Project <b className="text-ink">{r.project}</b> · base <Mono>{short(r.base_commit)}</Mono> · branch <Mono>{r.branch ?? "not yet"}</Mono> · updated {ago(r.updated_at)}{r.experiment ? " · eval experiment" : ""}</>}
        actions={!TERMINAL.includes(r.status) ? (
          <ActionButton variant="danger" action={async () => {
            const reason = window.prompt("Why cancel this run?");
            if (reason === null) return;
            await api(`/api/runs/${r.id}/cancel`, { method: "POST", body: { reason } });
            refresh();
          }}>Cancel run</ActionButton>
        ) : null}
      />
      <Card className="p-3"><Pipeline run={r} /></Card>
      <PreviewBox run={r} onChange={() => run.refetch()} />
      {r.reason && !r.waiting ? (
        <div className="flex items-start gap-2 rounded-xl border border-line bg-panel p-3 text-sm text-ink-2">
          {r.status === "COMPLETED" ? <Check className="mt-0.5 size-4 text-good" aria-hidden /> : TERMINAL.includes(r.status) ? <X className="mt-0.5 size-4 text-bad" aria-hidden /> : <AlertTriangle className="mt-0.5 size-4 text-muted" aria-hidden />}
          {r.reason}
        </div>
      ) : null}
      <DecisionPanel key={`${r.id}:${r.version}`} run={r} onDone={refresh} onShowSources={() => {
        select("sources");
        document.getElementById("run-tabs")?.scrollIntoView({ behavior: "smooth" });
      }} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[999_1_620px] flex-col gap-4">
          <div id="run-tabs" />
          <Tabs<Tab> value={shown} onChange={select} tabs={[
            ...(srs ? [] : [{ id: "tasks" as Tab, label: "Tasks", count: r.tasks.length }, { id: "plan" as Tab, label: "Plan", count: r.plans.length }]),
            ...(discovery ? [{ id: "sources" as Tab, label: "Sources", count: r.sources?.length ?? 0 },
              { id: "requirements" as Tab, label: "Requirements", count: [...r.documents].reverse().find((d) => d.kind === "inventory")?.inventory?.items.length ?? 0 },
              { id: "questions" as Tab, label: "Questions", count: r.questions?.filter((q) => q.status === "open").length ?? 0 },
              { id: "decisions" as Tab, label: "Decisions", count: r.decisions?.length ?? 0 }] : []),
            ...(r.documents.some((d) => d.kind === "acceptance") ? [{ id: "acceptance" as Tab, label: "Acceptance", count: r.documents.filter((d) => d.kind === "acceptance").length }] : []),
            { id: "thread", label: "Thread" },
            ...(srs ? [] : [{ id: "diff" as Tab, label: "Diff" }]),
            { id: "events", label: "Events" },
            { id: "work", label: "Jobs, logs & decisions" },
          ]} />
          {shown === "tasks" ? <TasksTab run={r} /> : null}
          {shown === "plan" ? (r.plans.length ? <Card className="p-5"><VersionPicker items={r.plans}>{(p) => <PlanBody plan={p} spec={spec} />}</VersionPicker></Card> : <Empty>No plan yet.</Empty>) : null}
          {shown === "sources" ? <SourcesTab run={r} onChange={refresh} /> : null}
          {shown === "requirements" ? <RequirementsTab run={r} /> : null}
          {shown === "questions" ? <QuestionsTab run={r} onChange={refresh} /> : null}
          {shown === "decisions" ? <DecisionsTab run={r} onChange={refresh} /> : null}
          {shown === "acceptance" ? (
            <Card className="p-5"><VersionPicker items={r.documents.filter((d) => d.kind === "acceptance")}>{(d) => <AcceptanceReport run={r} doc={d} />}</VersionPicker></Card>
          ) : null}
          {shown === "thread" ? <ThreadTab run={r} /> : null}
          {shown === "diff" ? <DiffTab run={r} /> : null}
          {shown === "events" ? <EventsTab run={r} /> : null}
          {shown === "work" ? <WorkTab run={r} /> : null}
        </div>
        <SidePanel run={r} />
      </div>
    </>
  );
}
