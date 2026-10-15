import { Check, CircleAlert, X } from "lucide-react";
import type { GateChecklist as Checklist } from "../lib/api";
import { cn } from "../lib/utils";

const AI_TONE: Record<string, string> = { pass: "text-good", "n/a": "text-muted", deferred: "text-warn", fail: "text-bad" };

/** Controller checks (block the approval when they fail), the AI role's self-check, and the items the reviewer ticks. */
export function GateChecklist({ checklist, checked, onChange }: { checklist: Checklist; checked: string[]; onChange: (ids: string[]) => void }) {
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
                <span>{c.text}{c.detail ? <span className="block text-xs text-bad">{c.detail}</span> : null}</span>
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
