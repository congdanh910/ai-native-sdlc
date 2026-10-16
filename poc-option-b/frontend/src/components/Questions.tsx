import { useState } from "react";
import { Download } from "lucide-react";
import { api, type DiscoveryDecision, type Question, type RunDetail } from "../lib/api";
import { ActionButton, Badge, Button, Card, Empty, Select, Textarea } from "./ui";

const TONE = { open: "accent", answered: "good", deferred: "warn", withdrawn: "neutral" } as const;

function roleName(run: RunDetail, id: string): string {
  return run.roles?.find((r) => r.id === id)?.name ?? id;
}

function QuestionCard({ run, q, open, onChange }: { run: RunDetail; q: Question; open: boolean; onChange: () => void }) {
  const [answer, setAnswer] = useState("");
  const post = async (path: string, body: unknown) => {
    await api(`/api/runs/${run.id}/questions/${q.id}/${path}`, { method: "POST", body });
    setAnswer("");
    onChange();
  };
  const withReason = (status: string, verb: string) => async () => {
    const reason = window.prompt(`Why is ${q.number} ${verb}?`);
    if (reason) await post("status", { status, reason });
  };
  return (
    <li className="flex list-none flex-col gap-2 border-b border-line p-4 last:border-0">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <b className="font-mono text-xs">{q.number}</b><Badge>{q.kind}</Badge>
        {q.blocking ? <Badge tone="bad">blocking</Badge> : null}
        <Badge tone={TONE[q.status]}>{q.status}</Badge>
        {q.items.length ? <span className="text-xs text-muted">about {q.items.join(", ")}</span> : null}
      </div>
      <b>{q.question}</b>
      {q.why ? <span className="text-sm text-muted">{q.why}</span> : null}
      {q.suggestion ? <span className="text-sm text-muted">Suggestion: {q.suggestion}</span> : null}
      {q.answer ? <div className="rounded-md bg-good-soft px-3 py-2 text-sm">{q.answer}<span className="ml-2 text-xs text-muted">· {q.answered_by}</span></div> : null}
      {q.status_reason && q.status !== "answered" ? <span className="text-xs text-muted">{q.status}: {q.status_reason}</span> : null}
      {open && q.status === "open" ? (
        <div className="flex flex-col gap-2">
          <Textarea aria-label={`Answer to ${q.number}`} className="min-h-16" placeholder={q.suggestion ?? "Your answer, or what the customer said (say who)"} value={answer} onChange={(e) => setAnswer(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2">
            <ActionButton variant="primary" disabled={!answer.trim()} success={`${q.number} answered`} action={() => post("answer", { answer })}>Answer</ActionButton>
            <ActionButton action={withReason("deferred", "deferred")}>Defer</ActionButton>
            <ActionButton action={withReason("withdrawn", "withdrawn")}>Withdraw</ActionButton>
            <Select aria-label="Move to role" className="h-9 w-auto" value="" onChange={async (e) => {
              const role = e.target.value;
              if (role) await post("reassign", { role, reason: window.prompt(`Why does ${roleName(run, role)} answer ${q.number}?`) ?? "" });
            }}>
              <option value="">Move to…</option>
              {run.roles?.filter((r) => r.id !== q.owner_role).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </Select>
          </div>
        </div>
      ) : null}
      {open && (q.status === "deferred" || q.status === "withdrawn") ? (
        <div><ActionButton action={withReason("open", "opened again")}>Open again</ActionButton></div>
      ) : null}
    </li>
  );
}

/** Discovery questions grouped by the role that answers them; the open ones first. */
export function QuestionsTab({ run, onChange }: { run: RunDetail; onChange: () => void }) {
  const questions = run.questions ?? [];
  const open = run.status === "CLARIFYING";
  const [role, setRole] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  if (!questions.length) return <Empty>No questions yet. They come with the requirement inventory after an extraction.</Empty>;
  const roles = (run.roles ?? []).filter((r) => questions.some((q) => q.owner_role === r.id));
  const shown = questions.filter((q) => (!role || q.owner_role === role) && (showClosed || q.status === "open"));
  const counts = (id: string) => questions.filter((q) => q.owner_role === id && q.status === "open").length;
  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-wrap items-center gap-2 p-3 text-sm">
        <Select aria-label="Role" className="h-8 w-auto" value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="">Every role ({questions.filter((q) => q.status === "open").length} open)</option>
          {roles.map((r) => <option key={r.id} value={r.id}>{r.name} ({counts(r.id)} open)</option>)}
        </Select>
        <label className="flex items-center gap-1.5 text-muted"><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />show answered, deferred, withdrawn</label>
        <a className="ml-auto" href={`/api/runs/${run.id}/workshop.md${role ? `?roles=${role}` : ""}`} download={`workshop-${role || "all"}.md`}>
          <Button className="h-8 px-2 text-xs"><Download className="size-3.5" aria-hidden />Workshop list</Button>
        </a>
      </Card>
      {(role ? roles.filter((r) => r.id === role) : roles).map((r) => {
        const mine = shown.filter((q) => q.owner_role === r.id);
        return mine.length ? (
          <Card key={r.id} className="overflow-hidden">
            <h3 className="m-0 border-b border-line bg-side px-4 py-2.5 text-sm font-semibold">{r.name} · {counts(r.id)} open</h3>
            <ul className="m-0 p-0">{mine.map((q) => <QuestionCard key={q.id} run={run} q={q} open={open} onChange={onChange} />)}</ul>
          </Card>
        ) : null;
      })}
      {!shown.length ? <Empty>No open questions{role ? ` for ${roleName(run, role)}` : ""}.</Empty> : null}
      {!open ? <p className="m-0 text-xs text-muted">Questions are answered while the discovery is CLARIFYING.</p> : null}
    </div>
  );
}

function DecisionRow({ run, d, onChange }: { run: RunDetail; d: DiscoveryDecision; onChange: () => void }) {
  const source = run.sources?.find((s) => s.id === d.source);
  const decide = (accept: boolean) => async () => {
    const reason = accept ? "" : window.prompt(`Why is ${d.id} rejected?`);
    if (!accept && !reason) return;
    await api(`/api/runs/${run.id}/decisions/${d.number}`, { method: "POST", body: { accept, reason } });
    onChange();
  };
  return (
    <tr className="align-top">
      <td className="border-b border-line px-3 py-2 font-mono text-xs">{d.id}</td>
      <td className="border-b border-line px-3 py-2">{d.text}{d.items.length ? <div className="text-xs text-muted">about {d.items.join(", ")}</div> : null}</td>
      <td className="border-b border-line px-3 py-2 text-xs text-muted">
        {d.basis === "answer" ? `answer to ${d.question}` : d.basis === "direct" ? "taken directly" : <>minutes {source ? <a href={`/api/runs/${run.id}/sources/${source.id}/file`} target="_blank" rel="noopener">{source.filename}</a> : null} {d.location}{d.question ? `, answers ${d.question}` : ""}</>}
      </td>
      <td className="border-b border-line px-3 py-2 text-xs">{roleName(run, d.role)}</td>
      <td className="border-b border-line px-3 py-2"><Badge tone={d.status === "accepted" ? "good" : d.status === "rejected" ? "bad" : "warn"}>{d.status}</Badge>
        {d.reason ? <div className="text-xs text-muted">{d.reason}</div> : null}</td>
      <td className="border-b border-line px-3 py-2 text-right whitespace-nowrap">
        {d.status === "proposed" && run.status === "CLARIFYING" ? <>
          <ActionButton className="mr-1 h-7 px-2 text-xs" variant="primary" action={decide(true)}>Accept</ActionButton>
          <ActionButton className="h-7 px-2 text-xs" variant="danger" action={decide(false)}>Reject</ActionButton>
        </> : <span className="text-xs text-muted">{d.decided_by ?? ""}</span>}
      </td>
    </tr>
  );
}

/** The decision log: answers to questions and decisions read from minutes, which their owner accepts or rejects. */
function NewDecision({ run, onChange }: { run: RunDetail; onChange: () => void }) {
  const [form, setForm] = useState({ text: "", items: "", role: run.roles?.[0]?.id ?? "po" });
  return (
    <details className="rounded-xl border border-line bg-panel p-3">
      <summary className="cursor-pointer text-sm font-semibold">Record a decision</summary>
      <div className="mt-2 flex flex-col gap-2">
        <span className="text-xs text-muted">For example "REQ-002 is out of scope for phase 1". It counts at once; Extract again applies it to the inventory.</span>
        <Textarea aria-label="Decision" className="min-h-14" value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} placeholder="What was decided" />
        <div className="flex flex-wrap gap-2">
          <input aria-label="Items" className="h-9 min-w-56 flex-1 rounded-md border border-line bg-panel px-2 text-sm" value={form.items} onChange={(e) => setForm({ ...form, items: e.target.value })} placeholder="REQ ids, comma-separated" />
          <Select aria-label="Owner" className="h-9 w-auto" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {run.roles?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select>
          <ActionButton variant="primary" disabled={!form.text.trim()} success="Decision recorded" action={async () => {
            await api(`/api/runs/${run.id}/decisions`, { method: "POST", body: { text: form.text, items: form.items.split(",").map((i) => i.trim()).filter(Boolean), role: form.role } });
            setForm({ ...form, text: "", items: "" });
            onChange();
          }}>Record</ActionButton>
        </div>
      </div>
    </details>
  );
}

export function DecisionsTab({ run, onChange }: { run: RunDetail; onChange: () => void }) {
  const decisions = run.decisions ?? [];
  const form = run.status === "CLARIFYING" ? <NewDecision run={run} onChange={onChange} /> : null;
  if (!decisions.length) return <div className="flex flex-col gap-3">{form}<Empty>No decisions yet. Answer a question, record one, or add minutes of a meeting as a source and extract again.</Empty></div>;
  return (
    <div className="flex flex-col gap-3">{form}
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead><tr className="bg-side text-left text-xs text-muted">{["ID", "Decision", "Basis", "Owner", "Status", ""].map((h, i) => <th key={i} className="border-b border-line px-3 py-2.5 font-semibold">{h}</th>)}</tr></thead>
        <tbody>{decisions.map((d) => <DecisionRow key={d.id} run={run} d={d} onChange={onChange} />)}</tbody>
      </table>
      <p className="m-0 px-4 py-2 text-xs text-muted">Accepted decisions change the inventory at the next extraction (Extract again).</p>
    </Card>
    </div>
  );
}
