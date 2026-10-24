import { Check, CircleDot, Lock, X } from "lucide-react";
import type { RunDetail } from "../lib/api";
import { cn } from "../lib/utils";

type State = "done" | "current" | "waiting" | "stopped" | "todo";
interface Stage { key: string; label: string; detail: string; gate?: boolean; state: State }

/** Where the run is, read from what it produced (documents, approvals, tasks, PR), not from one status field. */
export function stages(run: RunDetail): Stage[] {
  const has = (kind: string) => run.documents.some((d) => d.kind === kind);
  const approved = (stage: string) => run.approvals.some((a) => a.stage === stage && a.decision === "approve");
  if (run.kind === "discovery") return discoveryStages(run, has, approved);
  if (run.kind !== "feature" && run.kind !== "foundation") return documentStages(run, has, approved);
  const plan = run.plans[run.plans.length - 1];
  const passed = run.tasks.filter((t) => t.status === "PASSED").length;
  const review = [...run.documents].reverse().find((d) => d.kind === "review");
  const verdict = review?.content.match(/Verdict: \*\*(\w+)\*\*/)?.[1];
  const pr = run.pull_request;
  const foundation = run.kind === "foundation";
  const onboarded = approved("onboarding");
  const ready = has("spec") || (onboarded && !["DESIGNING_ONBOARDING", "WAITING_ONBOARDING_APPROVAL", "PREPARING_ENVIRONMENT", "NEEDS_HUMAN"].includes(run.status));
  const done = [
    has("intent"),
    ...(foundation ? [onboarded, ready] : []),
    has("spec"),
    approved("spec") || run.plans.length > 0,
    run.plans.length > 0 && !!plan?.grounding,
    approved("plan") || run.tasks.length > 0,
    approved("tests") || run.tasks.length > 0,
    run.tasks.length > 0 && passed === run.tasks.length,
    !!review,
    ...(run.preview ? [approved("acceptance") || !!pr] : []),
    !!pr && (pr.merged_sha !== null || run.status === "COMPLETED"),
  ];
  const list: Omit<Stage, "state">[] = [
    { key: "intent", label: "Intent", detail: foundation ? "from SDS.md" : "Product Owner" },
    ...(foundation ? [
      { key: "onboarding", label: "Onboarding", detail: onboarded ? "files approved" : "Architect writes, Tech Lead approves", gate: true },
      { key: "environment", label: "Environment", detail: ready ? "image and services work" : "build and try" },
    ] : []),
    { key: "spec", label: "Spec", detail: has("spec") ? "Business Analyst" : "not yet" },
    { key: "gate1", label: "Gate 1", detail: approved("spec") ? "spec approved" : "approve spec", gate: true },
    { key: "plan", label: "Plan", detail: plan ? `${plan.plan.tasks.length} tasks · grounding ${plan.grounding?.status ?? "…"}` : "not yet" },
    { key: "gate2", label: "Gate 2", detail: approved("plan") ? "plan approved" : "approve plan", gate: true },
    { key: "quality", label: "Quality", detail: approved("tests") ? "test cases approved" : "QC reviews test cases", gate: true },
    { key: "coding", label: "Coding", detail: run.tasks.length ? `${passed}/${run.tasks.length} tasks passed` : "after the Quality gate" },
    { key: "review", label: "Review", detail: review ? `verdict ${verdict ?? "?"}` : "after coding" },
    ...(run.preview ? [{ key: "acceptance", label: "Acceptance", detail: approved("acceptance") ? "QC accepted" : has("acceptance") ? "QC tries the preview" : "E2E on the preview", gate: true }] : []),
    { key: "gate3", label: "Gate 3", detail: pr?.merged_sha ? "merged" : pr ? (pr.simulated ? "simulated PR" : `PR #${pr.number}`) : "merge PR", gate: true },
  ];
  return withState(run, list, done);
}

/** A discovery: sources, extraction, clarification rounds, the PO's baseline, then the PR with requirements/. */
function discoveryStages(run: RunDetail, has: (kind: string) => boolean, approved: (stage: string) => boolean): Stage[] {
  const pr = run.pull_request;
  const sources = run.sources?.length ?? 0;
  const done = [sources > 0 && run.status !== "INGESTING", has("inventory"), approved("baseline") || run.status === "WAITING_BASELINE_APPROVAL",
    approved("baseline"), !!pr && (pr.merged_sha !== null || run.status === "COMPLETED")];
  const list: Omit<Stage, "state">[] = [
    { key: "sources", label: "Sources", detail: sources ? `${sources} file(s)` : "add raw requirements" },
    { key: "extract", label: "Extract", detail: has("inventory") ? "requirement inventory" : "Business Analyst" },
    { key: "clarify", label: "Clarify", detail: "questions per role" },
    { key: "baseline", label: "Baseline", detail: approved("baseline") ? "approved" : "PO approves", gate: true },
    { key: "pr", label: "PR", detail: pr?.merged_sha ? "merged" : pr ? `PR #${pr.number}` : "requirements/ to the repo", gate: true },
  ];
  return withState(run, list, done);
}

/** A project document run: brief, Business Analyst writes SRS.md (or Architect writes SDS.md), gate, PR. */
function documentStages(run: RunDetail, has: (kind: string) => boolean, approved: (stage: string) => boolean): Stage[] {
  const pr = run.pull_request;
  const srs = run.kind === "srs";
  const kind = srs ? "srs" : "sds", name = srs ? "SRS" : "SDS";
  const options = !srs && (has("options") || run.status === "DESIGNING_OPTIONS");
  const ui = !srs && (has("ui_concepts") || run.status === "DESIGNING_UI");
  const done = [has("intent"), ...(options ? [approved("options")] : []), ...(ui ? [approved("ui")] : []), has(kind), approved("spec"),
    !!pr && (pr.merged_sha !== null || run.status === "COMPLETED")];
  const list: Omit<Stage, "state">[] = [
    { key: "brief", label: "Brief", detail: srs ? "Product Owner" : "architecture brief" },
    ...(options ? [{ key: "options", label: "Options", detail: approved("options") ? "SA chose" : "Architect compares, SA picks", gate: true }] : []),
    ...(ui ? [{ key: "ui", label: "UI concept", detail: approved("ui") ? "PO chose" : "UX Designer proposes, PO picks", gate: true }] : []),
    { key: kind, label: name, detail: has(kind) ? (srs ? "Business Analyst" : "Architect") : "not yet" },
    { key: "gate1", label: "Gate 1", detail: approved("spec") ? `${name} approved` : `approve ${name}`, gate: true },
    { key: "gate2", label: "Gate 2", detail: pr?.merged_sha ? "merged" : pr ? (pr.simulated ? "simulated PR" : `PR #${pr.number}`) : "merge PR", gate: true },
  ];
  return withState(run, list, done);
}

function withState(run: RunDetail, list: Omit<Stage, "state">[], done: boolean[]): Stage[] {
  const current = done.findIndex((d) => !d);
  const stopped = ["NEEDS_HUMAN", "FAILED", "CANCELLED", "CLOSED_UNMERGED"].includes(run.status);
  const waiting = run.status.startsWith("WAITING") || run.status === "NEEDS_CLARIFICATION" || !!run.waiting;
  return list.map((stage, i) => ({
    ...stage,
    state: done[i] ? "done" : i === current ? (stopped ? "stopped" : waiting ? "waiting" : "current") : "todo",
  }));
}

const ICON: Record<State, typeof Check> = { done: Check, current: CircleDot, waiting: Lock, stopped: X, todo: CircleDot };

export function Pipeline({ run }: { run: RunDetail }) {
  return (
    <ol className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label="Pipeline">
      {stages(run).map((stage) => {
        const Icon = ICON[stage.state];
        return (
          <li key={stage.key} className={cn(
            "flex min-w-0 flex-[1_1_130px] flex-col gap-1 rounded-xl border px-3 py-2.5",
            stage.state === "done" && (stage.gate ? "border-accent/30 bg-accent-soft/50" : "border-line bg-panel"),
            stage.state === "todo" && "border-dashed border-line bg-transparent text-muted",
            stage.state === "current" && "border-accent bg-panel",
            stage.state === "waiting" && "border-2 border-accent bg-accent-soft",
            stage.state === "stopped" && "border-2 border-bad bg-bad-soft",
          )}>
            <div className="flex items-center gap-1.5 font-semibold">
              <Icon aria-hidden className={cn("size-3.5",
                stage.state === "done" && "text-good",
                stage.state === "current" && "animate-pulse text-accent",
                stage.state === "waiting" && "text-accent-ink",
                stage.state === "stopped" && "text-bad")} />
              {stage.label}
            </div>
            <div className="truncate text-xs text-muted">{stage.detail}</div>
          </li>
        );
      })}
    </ol>
  );
}
