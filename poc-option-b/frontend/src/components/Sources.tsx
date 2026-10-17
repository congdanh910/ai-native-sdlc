import { useRef, useState } from "react";
import { FileText, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, uploadFile, type RunDetail, type SourceFile } from "../lib/api";
import { short, when } from "../lib/utils";
import { ActionButton, Button, Card, Empty, Field, Input, Mono, Textarea } from "./ui";

const OPEN = ["INGESTING", "CLARIFYING"];

function size(bytes: number): string {
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

/** The raw requirement sources of a discovery: upload, paste, open, read the text view, remove with a reason. */
export function SourcesTab({ run, onChange }: { run: RunDetail; onChange: () => void }) {
  const open = OPEN.includes(run.status);
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [paste, setPaste] = useState({ title: "", text: "" });
  const [minutes, setMinutes] = useState(false);  // the next files or text are minutes of a meeting
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const sources = run.sources ?? [];

  const send = async (files: FileList | File[]) => {
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        try {
          await uploadFile(`/api/runs/${run.id}/sources?filename=${encodeURIComponent(file.name)}${minutes ? "&purpose=minutes" : ""}`, file);
          toast.success(`${file.name} added`);
        } catch (e) {
          toast.error(`${file.name}: ${(e as Error).message}`);
        }
      }
    } finally {
      setBusy(false);
      onChange();
    }
  };
  const showText = async (source: SourceFile) => {
    const data = await api<{ filename: string; text: string }>(`/api/runs/${run.id}/sources/${source.id}/text`);
    setPreview({ name: data.filename, text: data.text });
  };

  return (
    <div className="flex flex-col gap-3">
      {open ? (
        <Card className="flex flex-col gap-3 p-4">
          <div
            role="button" tabIndex={0}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); send(e.dataTransfer.files); }}
            className={`flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm ${dragging ? "border-accent bg-accent-soft" : "border-line"}`}
          >
            <Upload className="size-5 text-muted" aria-hidden />
            <b>{busy ? "Uploading…" : "Drop files here or click to choose"}</b>
            <span className="text-xs text-muted">Slides, documents, sheets, images and minutes: PDF, DOCX, XLSX, PPTX, PNG, JPG, MD, TXT. Each file is stored with its SHA-256.</span>
            <input ref={input} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) send(e.target.files); e.target.value = ""; }} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={minutes} onChange={(e) => setMinutes(e.target.checked)} />
            These are <b>minutes of a meeting</b> (or an email or chat with decisions): the Business Analyst reads the decisions in them at the next extraction, and their owners accept or reject each one.
          </label>
          <details>
            <summary className="cursor-pointer text-sm font-semibold">Paste text (an email, chat or meeting notes)</summary>
            <div className="mt-2 flex flex-col gap-2">
              <Field label="Title" htmlFor="paste-title"><Input id="paste-title" value={paste.title} onChange={(e) => setPaste({ ...paste, title: e.target.value })} placeholder="Call with sales, 16/10" /></Field>
              <Field label="Text" htmlFor="paste-text"><Textarea id="paste-text" className="min-h-32" value={paste.text} onChange={(e) => setPaste({ ...paste, text: e.target.value })} /></Field>
              <div>
                <ActionButton success="Text added as a source" action={async () => {
                  await api(`/api/runs/${run.id}/sources/text`, { method: "POST", body: { title: paste.title || "Pasted text", text: paste.text, purpose: minutes ? "minutes" : "requirements" } });
                  setPaste({ title: "", text: "" });
                  onChange();
                }}>Add as a source</ActionButton>
              </div>
            </div>
          </details>
        </Card>
      ) : null}
      {sources.length ? (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[680px] border-collapse text-sm">
            <thead><tr className="bg-side text-left text-xs text-muted">{["File", "Type", "Pages", "Size", "SHA-256", "Added", ""].map((h, i) => <th key={i} className="border-b border-line px-3 py-2.5 font-semibold">{h}</th>)}</tr></thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="border-b border-line px-3 py-2"><a href={`/api/runs/${run.id}/sources/${s.id}/file`} target="_blank" rel="noopener" className="font-semibold">{s.filename}</a></td>
                  <td className="border-b border-line px-3 py-2 uppercase text-muted">{s.kind}{s.purpose === "minutes" ? <span className="ml-1 normal-case text-accent-ink">· minutes</span> : null}</td>
                  <td className="border-b border-line px-3 py-2">{s.pages ?? "?"}</td>
                  <td className="border-b border-line px-3 py-2 text-muted">{size(s.size)}</td>
                  <td className="border-b border-line px-3 py-2"><Mono className="text-muted">{short(s.sha256, 12)}</Mono></td>
                  <td className="border-b border-line px-3 py-2 text-xs text-muted">{s.uploaded_by} · {when(s.created_at)}</td>
                  <td className="border-b border-line px-3 py-2 text-right whitespace-nowrap">
                    {s.has_text ? <Button className="mr-1 h-7 px-2 text-xs" onClick={() => showText(s)}><FileText className="size-3.5" aria-hidden />Text</Button> : null}
                    {open ? <ActionButton className="h-7 px-2 text-xs" variant="danger" action={async () => {
                      const reason = window.prompt(`Why remove ${s.filename}? It stays in the audit trail.`);
                      if (!reason) return;
                      await api(`/api/runs/${run.id}/sources/${s.id}/remove`, { method: "POST", body: { reason } });
                      onChange();
                    }}>Remove</ActionButton> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : <Empty>No sources yet. Add the raw requirements from sales or product: decks, documents, sheets, minutes.</Empty>}
      {preview ? (
        <Card className="flex flex-col gap-2 p-4">
          <div className="flex items-center gap-2 text-sm"><b>Text view: {preview.name}</b><span className="text-xs text-muted">locations in brackets are what requirement items cite</span>
            <button type="button" className="ml-auto text-xs text-muted hover:text-ink" onClick={() => setPreview(null)}>Close</button></div>
          <pre className="m-0 max-h-[420px] overflow-auto rounded-md bg-sunken p-3 font-mono text-[12px] whitespace-pre-wrap">{preview.text}</pre>
        </Card>
      ) : null}
    </div>
  );
}
