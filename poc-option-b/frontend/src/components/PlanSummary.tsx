import { Fragment, type ReactNode } from "react";
import { Badge } from "./ui";

// The Tech Lead writes the plan summary as one long paragraph. For reading, it is split into an overview,
// one block per task it mentions (T1, T2, ...) and notes about scope; the words are not changed.

const SENTENCE = /(?<=[.!?])\s+(?=[A-Z`(])/;
const TASK_START = /^(T\d+)\b/;
const NOTE = /out of scope|not in scope|left open|leaves? (them|it) open|QA Engineer|tests are written|assum|risk|follow-up/i;
// Things a reader scans for: `quoted code`, file paths, module:names, dotted calls, ENV_VARS and HTTP codes.
const TOKEN = /(`[^`]+`|\b[\w-]+(?:\/[\w.-]+)+\.\w+\b|\b[\w-]+\.(?:js|ts|tsx|py|json|md|yaml|css|html)\b|\bnode:[\w/]+|\b[a-z][A-Za-z0-9]*\.[a-z][A-Za-z0-9]*(?:\(\))?|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\bAC-\d+\b)/g;

export function Rich({ text }: { text: string }) {
  const parts = text.split(TOKEN);
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>;
        if (/^AC-\d+$/.test(part)) return <b key={i} className="font-semibold text-ink">{part}</b>;
        return <code key={i} className="rounded bg-sunken px-1 py-px font-mono text-[12.5px] text-ink">{part.replace(/^`|`$/g, "")}</code>;
      })}
    </>
  );
}

export function splitSummary(summary: string) {
  const sentences = summary.trim().split(SENTENCE).filter(Boolean);
  const overview: string[] = [];
  const tasks: { id: string; sentences: string[] }[] = [];
  const notes: string[] = [];
  for (const sentence of sentences) {
    const start = TASK_START.exec(sentence);
    if (start) {
      const text = sentence.replace(TASK_START, "").replace(/^[\s:,-]+/, "");
      const same = tasks.find((t) => t.id === start[1]);  // "T1 creates ... T1 also creates ..." stays one block
      if (same) same.sentences.push(text);
      else tasks.push({ id: start[1], sentences: [text] });
    }
    else if (NOTE.test(sentence)) notes.push(sentence);
    else if (tasks.length) tasks[tasks.length - 1].sentences.push(sentence);
    else overview.push(sentence);
  }
  return { overview, tasks, notes };
}

export function PlanSummary({ summary, titles = {} }: { summary: string; titles?: Record<string, string> }) {
  if (!summary.trim()) return null;
  const { overview, tasks, notes } = splitSummary(summary);
  const block = (sentences: string[]): ReactNode => sentences.map((s, i) => <span key={i}>{i ? " " : ""}<Rich text={s} /></span>);
  return (
    <div className="flex flex-col gap-3 text-[14px] leading-relaxed text-ink-2">
      {overview.length ? <p className="m-0 max-w-[90ch]">{block(overview)}</p> : null}
      {tasks.length ? (
        <ol className="m-0 flex list-none flex-col gap-2 p-0">
          {tasks.map((task, i) => (
            <li key={`${task.id}-${i}`} className="flex gap-3 rounded-lg border border-line bg-side px-3 py-2.5">
              <Badge tone="accent" className="mt-0.5 h-fit font-mono">{task.id}</Badge>
              <div className="min-w-0 max-w-[90ch]">
                {titles[task.id] ? <div className="font-semibold text-ink">{titles[task.id]}</div> : null}
                <div>{block(task.sentences)}</div>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
      {notes.length ? (
        <div className="rounded-lg bg-sunken px-3 py-2.5">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Notes</div>
          <ul className="m-0 flex flex-col gap-1 pl-4">{notes.map((n, i) => <li key={i} className="max-w-[90ch]"><Rich text={n} /></li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}
