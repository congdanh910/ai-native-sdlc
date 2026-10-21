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
    <div className="flex flex-col">
      <p className="m-0 border-b border-line bg-side px-3 py-2 text-xs text-muted">
        <b className="text-ink-2">How to read:</b> each row is an acceptance criterion of the spec, each column a task of the plan (hover a task for its title and files).{" "}
        <span className="inline-block size-2.5 rounded bg-accent align-middle" /> the task delivers the criterion;{" "}
        <span className="inline-block size-2.5 rounded border border-dashed border-line-strong align-middle" /> it does not.{" "}
        <b className="text-ink-2">Test cases</b> are the numbers of the QA Analyst's cases (TC-n, see the Tests tab) that prove the criterion.
        Check that every row has at least one task and one test; <i>no task</i> or <i>no test</i> marks a gap.
      </p>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead>
          <tr className="bg-side text-left text-xs text-muted">
            <th className="w-[46%] border-b border-line px-3 py-2.5 font-semibold">Acceptance criterion</th>
            {tasks.map((t) => (
              <th key={t.id} className="border-b border-line px-3 py-2.5 align-bottom font-semibold"
                title={`${t.id}: ${t.title}\n\n${[...t.modify.map((f) => `modify ${f}`), ...t.create.map((f) => `create ${f}`)].join("\n")}`}>
                <div>{t.id}</div>
                <div className="line-clamp-3 max-w-[180px] font-normal text-ink-2">{t.title}</div>
                <div className="mt-1 text-[11px] font-normal">{t.modify.length + t.create.length} file(s)</div>
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
                  {proving.length ? <span className="flex flex-wrap gap-1">{proving.map((c) => (
                      <span key={c.id} title={`${c.id}: ${c.description}\nExpected: ${c.expected}\nFile: ${c.target_file}`}><Mono>{c.id.replace("TC-", "")}</Mono></span>
                    ))}</span>
                    : <Badge tone="bad">no test</Badge>}
                  {!delivered ? <Badge tone="bad" className="ml-2">no task</Badge> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-xs text-muted">
        <li><b className="text-ink-2">Grounding</b>: the controller checked that every file, function and import the plan names exists in the repository or is created by a task of the plan. A fail lists what does not exist; the Tech Lead is asked once to fix it.</li>
        <li><b className="text-ink-2">Warnings</b>: what the controller suggests you look at, without blocking (for example markup changed without its stylesheet).</li>
        <li><b className="text-ink-2">Expected pass</b>: test cases written to pass before any code (guards of behaviour that already exists). Every other new test must fail first (the red check), which proves it tests the new code.</li>
      </ul>
      {g?.findings.map((f) => <div key={f} className="flex gap-2 text-sm text-bad"><XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />{f}</div>)}
      {g?.warnings?.map((w) => <div key={w} className="flex gap-2 text-sm text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />{w}</div>)}
    </div>
  );
}
