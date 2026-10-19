import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Doc } from "../lib/api";
import { cn } from "../lib/utils";
import { Badge, Button, ErrorBox, Loading } from "./ui";

const SECTIONS_SHOWN = 12;  // the most changed first; a full rewrite touches every section

/** A rewrite after the reviewer's feedback: what they asked, how the AI role says it handled each point, which
 *  sections changed, and the line diff from the rejected version, so the reviewer can check the answer. */
export function RevisionSummary({ runId, doc }: { runId: string; doc: Doc }) {
  const [diffShown, setDiffShown] = useState(false);
  const [allSections, setAllSections] = useState(false);
  const revision = doc.revision;
  if (!revision) return null;
  const base = revision.from_version;
  const sections = [...revision.sections].sort((a, b) => b.added + b.removed - (a.added + a.removed));
  const shown = allSections ? sections : sections.slice(0, SECTIONS_SHOWN);
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-accent/40 bg-accent-soft p-3 text-sm">
      <b>Changes since v{base}</b>
      {revision.feedback.length ? (
        <section>
          <h3 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted">What you asked</h3>
          <ul className="m-0 flex flex-col gap-1 pl-5">{revision.feedback.map((f, i) => <li key={i} className="whitespace-pre-wrap">{f}</li>)}</ul>
        </section>
      ) : null}
      <section>
        <h3 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted">How the AI role says it handled it</h3>
        {revision.replies.length ? (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {revision.replies.map((r, i) => (
              <li key={i} className="rounded-md border border-line bg-panel p-2">
                {r.feedback ? <div className="text-xs text-muted">{r.feedback}</div> : null}
                <div>{r.change}</div>
                {r.where.length ? <div className="mt-1 flex flex-wrap gap-1">{r.where.map((w) => <Badge key={w}>{w}</Badge>)}</div> : null}
              </li>
            ))}
          </ul>
        ) : <p className="m-0 text-warn">No reply to the feedback: check the sections below and the diff yourself.</p>}
      </section>
      <section>
        <h3 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Sections that changed ({sections.length}, most changed first)</h3>
        {revision.sections.length ? (
          <div className="flex flex-wrap gap-1.5">
            {shown.map((s) => (
              <span key={s.heading} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-panel px-2 py-0.5 text-xs">
                {s.heading}
                {s.state !== "changed" ? <Badge tone={s.state === "new" ? "good" : "bad"}>{s.state}</Badge> : null}
                <span className="font-mono text-good">+{s.added}</span><span className="font-mono text-bad">-{s.removed}</span>
              </span>
            ))}
            {sections.length > SECTIONS_SHOWN ? (
              <button type="button" className="text-xs text-accent-ink underline" onClick={() => setAllSections(!allSections)}>
                {allSections ? "show fewer" : `show all ${sections.length}`}
              </button>
            ) : null}
          </div>
        ) : <p className="m-0 text-warn">Nothing changed since v{base}.</p>}
      </section>
      <div><Button onClick={() => setDiffShown(!diffShown)}>{diffShown ? "Hide the diff" : `Show the diff with v${base}`}</Button></div>
      {diffShown ? <DocumentDiff runId={runId} kind={doc.kind} base={base} head={doc.version} /> : null}
    </div>
  );
}

function DocumentDiff({ runId, kind, base, head }: { runId: string; kind: string; base: number; head: number }) {
  const diff = useQuery({ queryKey: ["document-diff", runId, kind, base, head],
    queryFn: () => api<{ diff: string }>(`/api/runs/${runId}/documents/${kind}/diff?base=${base}&head=${head}`) });
  if (diff.isLoading) return <Loading />;
  if (diff.error) return <ErrorBox error={diff.error} />;
  if (!diff.data?.diff) return <p className="m-0 text-muted">No differences.</p>;
  return (
    <pre className="m-0 max-h-[60vh] overflow-auto rounded-md border border-line bg-panel font-mono text-[12px] leading-[1.55]">
      {diff.data.diff.split("\n").map((line, i) => (
        <div key={i} className={cn("whitespace-pre-wrap px-3",
          line.startsWith("+") && !line.startsWith("+++") && "bg-good-soft text-good",
          line.startsWith("-") && !line.startsWith("---") && "bg-bad-soft text-bad",
          line.startsWith("@@") && "bg-accent-soft text-accent-ink")}>{line || " "}</div>
      ))}
    </pre>
  );
}
