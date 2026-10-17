import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import type { PlanVersion } from "../lib/api";
import { cn, parseCriteria } from "../lib/utils";
import { Badge, Mono } from "./ui";

/** Acceptance criteria × tasks × test cases for one plan version: what a human checks before approving. */
export function CoverageMatrix({ plan, specMarkdown }: { plan: PlanVersion; specMarkdown?: string }) {
  const tasks = plan.plan.tasks;
  const cases = plan.test_plan.cases;
  const known = parseCriteria(specMarkdown);
  const ids = known.length ? known.map((c) => c.id)
    : [...new Set(tasks.flatMap((t) => t.acceptance_criteria))].sort((a, b) => Number(a.slice(3)) - Number(b.slice(3)));
  const text = Object.fromEntries(known.map((c) => [c.id, c.text]));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead>
          <tr className="bg-side text-left text-xs text-muted">
            <th className="w-[46%] border-b border-line px-3 py-2.5 font-semibold">Acceptance criterion</th>
            {tasks.map((t) => (
              <th key={t.id} className="border-b border-line px-3 py-2.5 font-semibold">
                {t.id} <span className="font-mono font-normal">{[...t.modify, ...t.create].map((f) => f.split("/").pop()).join(", ")}</span>
              </th>
            ))}
            <th className="border-b border-line px-3 py-2.5 font-semibold">Test cases</th>
          </tr>
        </thead>
        <tbody>
          {ids.map((id) => {
            const proving = cases.filter((c) => c.criterion === id);
            const guards = proving.filter((c) => c.expected_pass);
            const delivered = tasks.some((t) => t.acceptance_criteria.includes(id));
            return (
              <tr key={id} className={cn(guards.length ? "bg-flag-soft/60" : undefined)}>
                <td className="border-b border-line px-3 py-2.5 align-top">
                  <Mono className="text-muted">{id}</Mono> <span className="ml-1.5 text-ink-2">{text[id] ?? ""}</span>
                  {guards.map((g) => <Badge key={g.id} tone="flag" className="ml-2">{g.id} expected pass</Badge>)}
                </td>
                {tasks.map((t) => (
                  <td key={t.id} className="border-b border-line px-3 py-2.5">
                    {t.acceptance_criteria.includes(id)
                      ? <span className="inline-block size-3.5 rounded bg-accent" aria-label="delivered" />
                      : <span className="inline-block size-3.5 rounded border-[1.5px] border-dashed border-line-strong" aria-label="not in task" />}
                  </td>
                ))}
                <td className="border-b border-line px-3 py-2.5">
                  {proving.length ? <Mono>{proving.map((c) => c.id.replace("TC-", "")).join(", ")}</Mono>
                    : <Badge tone="bad">no test</Badge>}
                  {!delivered ? <Badge tone="bad" className="ml-2">no task</Badge> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function GroundingSummary({ plan }: { plan: PlanVersion }) {
  const g = plan.grounding;
  const guards = plan.test_plan.cases.filter((c) => c.expected_pass).length;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {g ? (g.status === "pass"
          ? <Badge tone="good"><CheckCircle2 className="size-3.5" aria-hidden />Grounding pass</Badge>
          : <Badge tone="bad"><XCircle className="size-3.5" aria-hidden />Grounding fail · {g.findings.length}</Badge>)
          : <Badge>Grounding not run</Badge>}
        <Badge tone={g?.warnings?.length ? "warn" : "neutral"}>{g?.warnings?.length ?? 0} warning(s)</Badge>
        <Badge tone={guards ? "flag" : "neutral"}>{guards} expected pass</Badge>
      </div>
      {g?.findings.map((f) => <div key={f} className="flex gap-2 text-sm text-bad"><XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />{f}</div>)}
      {g?.warnings?.map((w) => <div key={w} className="flex gap-2 text-sm text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />{w}</div>)}
    </div>
  );
}
