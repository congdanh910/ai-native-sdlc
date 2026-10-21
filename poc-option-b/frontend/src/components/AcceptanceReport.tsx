import { useState } from "react";
import type { Doc, RunDetail } from "../lib/api";
import { Badge } from "./ui";

interface Attachment { name: string; type: string; path: string }
interface Case { title: string; criteria: string[]; status: string; error: string; attachments: Attachment[] }

/** Each criterion of the story with its end-to-end tests, their outcome and their screenshots and videos. */
export function AcceptanceReport({ run, doc }: { run: RunDetail; doc: Doc }) {
  const data = (doc as Doc & { acceptance?: { cases: Case[]; criteria: Record<string, string>; attempt: number } }).acceptance;
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return null;
  const base = `/api/runs/${run.id}/evidence/${data.attempt}`;
  return (
    <div className="flex flex-col gap-2">
      {Object.entries(data.criteria).map(([id, text]) => {
        const cases = data.cases.filter((c) => c.criteria.includes(id));
        const passed = cases.length > 0 && cases.every((c) => c.status === "passed");
        return (
          <div key={id} className="rounded-lg border border-line p-3 text-sm">
            <div className="flex flex-wrap items-start gap-2">
              <Badge tone={passed ? "good" : cases.length ? "bad" : "warn"} dot>{passed ? "passed" : cases.length ? "failed" : "no test"}</Badge>
              <b>{id}</b><span className="text-ink-2">{text}</span>
            </div>
            {cases.map((c) => (
              <div key={c.title} className="mt-2 border-t border-line pt-2">
                <div className="text-xs text-muted">{c.title} · {c.status}</div>
                {c.error ? <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-bad-soft p-2 text-xs text-bad">{c.error}</pre> : null}
                <div className="mt-1 flex flex-wrap gap-2">
                  {c.attachments.map((a) => a.type.startsWith("image/") ? (
                    <button key={a.path} type="button" onClick={() => setOpen(`${base}/${a.path}`)} title={a.name}>
                      <img src={`${base}/${a.path}`} alt={a.name} className="h-24 rounded border border-line object-cover" />
                    </button>
                  ) : a.type.startsWith("video/") ? (
                    <video key={a.path} src={`${base}/${a.path}`} controls className="h-24 rounded border border-line" />
                  ) : (
                    <a key={a.path} href={`${base}/${a.path}`} target="_blank" rel="noreferrer" className="text-xs text-accent-ink underline">{a.name || a.path}</a>
                  ))}
                </div>
              </div>
            ))}
          </div>
        );
      })}
      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/90 p-6" role="dialog" aria-modal="true" onClick={() => setOpen(null)}>
          <img src={open} alt="evidence" className="max-h-full max-w-full rounded border border-line" />
        </div>
      ) : null}
    </div>
  );
}
