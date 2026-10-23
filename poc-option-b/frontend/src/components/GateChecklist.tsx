import { Check, CircleAlert, Lightbulb, X } from "lucide-react";
import type { GateChecklist as Checklist, GateFix } from "../lib/api";
import { ActionButton, Button } from "./ui";
import { cn } from "../lib/utils";

const AI_TONE: Record<string, string> = { pass: "text-good", "n/a": "text-muted", deferred: "text-warn", warning: "text-warn", fail: "text-bad" };

/** Controller checks (block the approval when they fail), the AI role's self-check, and the items the reviewer ticks. */
const ACTION: Record<GateFix["action"], { label: string; who: string }> = {
  replan: { label: "Reject: replan", who: "the Tech Lead AI plans again with this reason, then the test cases follow (about 10 minutes)" },
  redesign_tests: { label: "Reject: redesign tests", who: "the QA Analyst designs the test cases again with this reason (a few minutes)" },
  rewrite_spec: { label: "Reject: rewrite", who: "the Business Analyst writes the spec again with this reason" },
};

export function GateChecklist({ checklist, checked, onChange, onFix }: {
  checklist: Checklist; checked: string[]; onChange: (ids: string[]) => void;
  /** use: put the reason in the reason box; do: take the decision now */
  onFix?: (fix: GateFix, mode: "use" | "do" | "amend") => Promise<void> | void;
}) {
  const failing = checklist.controller.filter((c) => !c.ok).length;
  const toggle = (id: string) => onChange(checked.includes(id) ? checked.filter((c) => c !== id) : [...checked, id]);
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        <b>Review checklist</b>
        <span className="text-xs text-muted">from aif-review-checklist.md; recorded with your decision</span>
      </div>
      {checklist.controller.length ? (
        <section>
          <h3 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Checked by the controller{failing ? ` · ${failing} failing, approval blocked` : ""}</h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {checklist.controller.map((c) => (
              <li key={c.id} className="flex gap-2">
                {c.ok ? <Check className="mt-0.5 size-4 shrink-0 text-good" aria-label="passed" /> : <X className="mt-0.5 size-4 shrink-0 text-bad" aria-label="failed" />}
                <span className="min-w-0 flex-1">{c.text}{c.detail ? <span className="block text-xs text-bad">{c.detail}</span> : null}
                  {!c.ok && c.fixes?.length && onFix ? c.fixes.map((f, i) => <FixCard key={i} fix={f} onFix={onFix} />) : null}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {checklist.ai.length ? (
        <section>
          <h3 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Self-check by the AI role</h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {checklist.ai.map((a) => (
              <li key={a.id} className="flex gap-2">
                <span className={cn("w-20 shrink-0 font-mono text-xs leading-5", AI_TONE[a.status] ?? "text-muted")}>{a.status}</span>
                <span>{a.text}{a.note ? <span className="block text-xs text-muted">{a.note}</span> : null}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <section>
        <h3 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Your review · tick every item to approve</h3>
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {checklist.human.map((h) => (
            <li key={h.id}>
              <label className="flex cursor-pointer gap-2">
                <input type="checkbox" className="mt-1" checked={checked.includes(h.id)} onChange={() => toggle(h.id)} />
                <span>{h.text}</span>
              </label>
            </li>
          ))}
        </ul>
        {checked.length < checklist.human.length ? (
          <p className="m-0 mt-1 flex items-center gap-1 text-xs text-muted"><CircleAlert className="size-3.5" aria-hidden />{checklist.human.length - checked.length} item(s) left to tick</p>
        ) : null}
      </section>
    </div>
  );
}


/** What to do about one failing check: why it blocks, the quickest correct decision, and the reason ready to send. */
function FixCard({ fix, onFix }: { fix: GateFix; onFix: (fix: GateFix, mode: "use" | "do" | "amend") => Promise<void> | void }) {
  const action = ACTION[fix.action];
  return (
    <div className="mt-2 flex flex-col gap-2 rounded-lg border border-accent/40 bg-accent-soft/40 p-3 text-xs text-ink">
      <div className="flex items-center gap-1.5 font-semibold text-accent-ink"><Lightbulb className="size-3.5" aria-hidden />Next step{fix.finding ? <span className="font-normal text-muted"> · {fix.finding}</span> : null}</div>
      <p className="m-0">{fix.why}</p>
      {fix.amend ? (
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton variant="primary" success={`${fix.amend.criterion} added to ${fix.amend.task}; the checks run again`}
            title="A new plan version with this one change; the test cases stay as they are. No model is asked."
            action={async () => { await onFix(fix, "amend"); }}>Add {fix.amend.criterion} to {fix.amend.task} now</ActionButton>
          <span className="text-muted">recommended: one change, no AI, a second</span>
        </div>
      ) : null}
      <div className="rounded border border-line bg-panel p-2 font-mono text-[11.5px]">{fix.reason}</div>
      <div className="flex flex-wrap items-center gap-2">
        <ActionButton variant={fix.amend ? undefined : "primary"} action={async () => { await onFix(fix, "do"); }}>{action.label} with this reason</ActionButton>
        <Button onClick={() => onFix(fix, "use")}>Edit the reason first</Button>
        <span className="text-muted">{action.who}</span>
      </div>
    </div>
  );
}
