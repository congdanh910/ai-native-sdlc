import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, GitPullRequest } from "lucide-react";
import { api, projectDoc, type Doc, type Meta, type RunDetail } from "../lib/api";
import { ACTION_LABEL, role, short } from "../lib/utils";
import { CoverageMatrix, GroundingSummary } from "./Coverage";
import { GateChecklist } from "./GateChecklist";
import { PlanSummary } from "./PlanSummary";
import { RevisionSummary } from "./Revision";
import { ActionButton, Badge, Button, Card, Field, Markdown, Mono, Select, StatusBadge, Textarea } from "./ui";

function Panel({ run, title, children }: { run: RunDetail; title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4 border-2 border-accent/60 p-5">
      <div className="flex flex-wrap items-center gap-2.5">
        <Badge tone="accent">{role(run.waiting?.role)} · your decision</Badge>
        <h2 className="m-0 text-lg font-semibold">{title}</h2>
      </div>
      {children}
    </Card>
  );
}

function Reason({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <Field label="Reason" htmlFor="decision-reason">
      <Textarea id="decision-reason" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="min-h-16" />
    </Field>
  );
}

export function DecisionPanel({ run, onDone, onShowSources }: { run: RunDetail; onDone: () => void; onShowSources?: () => void }) {
  const need = run.waiting;
  const [reason, setReason] = useState("");
  const [beyond, setBeyond] = useState("");  // why a discovery needs a round past its limit
  const [editing, setEditing] = useState(false);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta"), staleTime: 60_000 });
  const post = async (path: string, body: unknown) => {
    await api(`/api/runs/${run.id}/${path}`, { method: "POST", body });
    setReason("");
    setEditing(false);
    onDone();
  };
  const doc = projectDoc(run);
  const srs = doc !== null;  // a project document (SRS or SDS) rather than a feature spec
  const spec = [...run.documents].reverse().find((d) => d.kind === (doc?.kind ?? "spec"));
  const specName = doc?.name ?? "spec.md";
  const [specDraft, setSpecDraft] = useState(spec?.content ?? "");
  const plan = run.plans[run.plans.length - 1];
  const [planDraft, setPlanDraft] = useState(plan ? JSON.stringify({ plan: plan.plan, test_plan: plan.test_plan }, null, 2) : "");
  const tests = [...run.documents].reverse().find((d) => d.kind === "tests");
  const [testsDraft, setTestsDraft] = useState(plan ? JSON.stringify(plan.test_plan, null, 2) : "");
  const [checked, setChecked] = useState<string[]>([]);
  const checklist = run.checklist ? <GateChecklist checklist={run.checklist} checked={checked} onChange={setChecked} /> : null;
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [classification, setClassification] = useState({ category: "", stage: "", role: "", note: "" });
  if (!need) return null;

  if (need.action === "start") {
    return (
      <Panel run={run} title={`Start ${need.stage}`}>
        <p className="m-0 text-muted">The {need.stage} stage is in manual mode and waits for you.</p>
        <div><ActionButton variant="primary" action={() => post("start-stage", { stage: need.stage })}>Start {need.stage}</ActionButton></div>
      </Panel>
    );
  }

  if (need.action === "ingesting" || need.action === "clarifying") {
    const sources = run.sources?.length ?? 0;
    const rounds = run.documents.filter((d) => d.kind === "inventory").length;
    return (
      <Panel run={run} title={need.action === "ingesting" ? "Add the sources, then start the extraction" : "Review the requirement inventory"}>
        <p className="m-0 text-muted">
          {need.action === "ingesting"
            ? `Add the raw requirements in the Sources tab (drag and drop, or paste text); ${sources} added so far. The Business Analyst then reads every source part by part and writes the requirement inventory; every item cites the page, slide or cell it comes from.`
            : (run.questions ?? []).some((q) => q.status === "open")
              ? `Inventory v${rounds} is in the Requirements tab (round ${run.rounds?.used ?? rounds} of ${run.rounds?.limit ?? "?"}). Answer the questions (Questions tab), add sources or minutes, then extract again: only what is new is read.`
              : `No open questions (inventory v${rounds}, round ${run.rounds?.used ?? rounds} of ${run.rounds?.limit ?? "?"}). Next: prepare the baseline below. If you recorded decisions after this version, extract again first so the inventory includes them.`}
        </p>
        {need.action === "clarifying" ? (
          <div className="flex flex-wrap gap-2 text-xs">
            {(run.roles ?? []).map((r) => {
              const n = run.questions?.filter((q) => q.owner_role === r.id && q.status === "open").length ?? 0;
              return n ? <Badge key={r.id} tone="accent">{r.name}: {n} open</Badge> : null;
            })}
            {run.questions?.some((q) => q.status === "open" && q.blocking) ? <Badge tone="bad">{run.questions.filter((q) => q.status === "open" && q.blocking).length} blocking</Badge> : null}
            {run.decisions?.some((d) => d.status === "proposed") ? <Badge tone="warn">{run.decisions.filter((d) => d.status === "proposed").length} decision(s) to accept</Badge> : null}
          </div>
        ) : null}
        {run.rounds && run.rounds.used >= run.rounds.limit ? (
          <div className="flex flex-col gap-2 rounded-lg border border-warn/40 bg-warn-soft p-3 text-sm">
            <span>All {run.rounds.limit} extraction rounds of the project's limit (discovery.limits.max_rounds) are used. The baseline needs no more rounds; for another one, confirm it as Product Owner with a reason.</span>
            <Field label="Why another round" htmlFor="beyond-limit"><Textarea id="beyond-limit" className="min-h-12" value={beyond} onChange={(e) => setBeyond(e.target.value)} placeholder="New minutes from the workshop of 20/10" /></Field>
            <div className="flex flex-wrap gap-2">
              <ActionButton disabled={!beyond.trim()} success="Extraction started" action={() => post("extract", { beyond_limit: beyond })}>Confirm another round: extract again</ActionButton>
              <ActionButton disabled={!beyond.trim()} success="The Business Analyst checks the inventory again" action={() => post("extract?again=true", { beyond_limit: beyond })}>Confirm another round: check again</ActionButton>
            </div>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {onShowSources ? <Button variant={sources ? "default" : "primary"} onClick={onShowSources}>{sources ? "Add more files" : "Add files"}</Button> : null}
          <ActionButton variant={sources ? "primary" : "default"} disabled={!sources} success="Extraction started" action={() => post("extract", {})}>
          {need.action === "ingesting" ? "Start extraction" : "Extract again"}</ActionButton>
          {need.action === "clarifying" ? <ActionButton success="The Business Analyst checks the inventory again" action={() => post("extract?again=true", {})}>Check the inventory again</ActionButton> : null}</div>
        {need.action === "clarifying" ? (
          <details className="rounded-lg border border-line p-3" open={!(run.questions ?? []).some((q) => q.status === "open")}>
            <summary className="cursor-pointer text-sm font-semibold">Prepare the baseline (Product Owner)</summary>
            <div className="mt-2 flex flex-col gap-2">
              <p className="m-0 text-sm text-muted">The baseline is inventory v{rounds} with the accepted decisions. At its gate the controller checks that no blocking question or conflict is open, every feature in scope has functions and quality targets have numbers; you tick the review items, then requirements/ goes to the repository through a draft PR.</p>
              <Field label="Scope of this phase" htmlFor="baseline-scope"><Textarea id="baseline-scope" className="min-h-16" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Phase 1: what this release covers, as agreed with the customer" /></Field>
              <div><ActionButton disabled={!reason.trim()} action={() => post("baseline", { scope: reason })}>Prepare baseline</ActionButton></div>
            </div>
          </details>
        ) : null}
      </Panel>
    );
  }

  if (need.action === "waiting_options_approval") {
    const doc = [...run.documents].reverse().find((d) => d.kind === "options");
    if (!doc) return null;
    return <OptionsPanel run={run} doc={doc} checklist={checklist} checked={checked} post={post} />;
  }

  if (need.action === "waiting_baseline_approval") {
    const baseline = [...run.documents].reverse().find((d) => d.kind === "baseline");
    if (!baseline) return null;
    return (
      <Panel run={run} title={`Baseline on inventory v${run.documents.filter((d) => d.kind === "inventory").length}`}>
        <p className="m-0 text-sm text-muted">Approving commits requirements/ (inventory, questions, decisions, baseline and the raw sources) to the run's branch and opens a draft PR. Merging it completes the discovery; the SRS run then builds on it.</p>
        <div className="max-h-[420px] overflow-y-auto rounded-lg border border-line bg-side p-4"><Markdown source={baseline.content} /></div>
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Required to send it back" />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant="primary" success="Baseline approved; opening the PR" action={() => post("baseline/approve", { document_id: baseline.id, checked })}>Approve baseline</ActionButton>
          <ActionButton variant="danger" action={() => post("baseline/reject", { reason })}>Back to clarifying</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "needs_clarification") {
    const open = run.clarifications.filter((c) => !c.answer);
    return (
      <Panel run={run} title="Answer the Business Analyst">
        {open.map((c) => (
          <Field key={c.id} label={c.question} htmlFor={`q-${c.id}`}>
            <Textarea id={`q-${c.id}`} value={answers[c.id] ?? ""} onChange={(e) => setAnswers({ ...answers, [c.id]: e.target.value })} />
          </Field>
        ))}
        <div><ActionButton variant="primary" action={() => post("answers", { answers })}>Send answers</ActionButton></div>
      </Panel>
    );
  }

  if (need.action === "waiting_spec_approval" && spec) {
    return (
      <Panel run={run} title={`${specName} v${spec.version}`}>
        {srs ? <p className="m-0 text-sm text-muted">Accepting commits {specName} to the repository root and opens a draft PR; {doc?.kind === "srs" ? "the SDS and the feature runs build on its section 7 user stories" : "feature runs design each story inside this architecture"}.</p> : null}
        {spec.findings?.length ? (
          <div className="rounded-lg border border-warn/40 bg-warn-soft p-3 text-sm">
            <b>Sync with SRS: {spec.findings.length} finding(s)</b>
            <ul className="mb-0 mt-1 pl-5">{spec.findings.map((f) => <li key={f}>{f}</li>)}</ul>
          </div>
        ) : null}
        <RevisionSummary runId={run.id} doc={spec} />
        <div className="max-h-[520px] overflow-y-auto rounded-lg border border-line bg-side p-4"><Markdown source={spec.content} /></div>
        {editing ? (
          <Field label={`Edit ${specName}`} hint={doc?.kind === "srs" ? "keep sections 1-8 and story headings like #### 7.1.1 AT-US-01: Title (ROLE)" : doc ? "keep sections 1-13 and a section 5 entry for every SRS story" : "keep criteria as - **AC-01 — Name:** followed by Given / When / Then lines"} htmlFor="spec-edit">
            <Textarea id="spec-edit" className="min-h-72 font-mono text-[12.5px]" value={specDraft} onChange={(e) => setSpecDraft(e.target.value)} />
          </Field>
        ) : null}
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Required to reject, optional to revise" />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant="primary" success={srs ? `${specName} accepted; opening the PR` : "Spec accepted"} action={() => post("approve-spec", { document_id: spec.id, sha256: spec.sha256, checked })}>
            Accept {srs ? specName : "spec"} v{spec.version}
          </ActionButton>
          <Button onClick={() => setEditing(!editing)}>{editing ? "Hide editor" : "Edit"}</Button>
          {editing ? <ActionButton action={() => post("revise-spec", { content: specDraft, reason: reason || null })}>Save as new version</ActionButton> : null}
          <ActionButton variant="danger" action={() => post("reject-spec", { reason, next: "redo" })}>Reject: rewrite</ActionButton>
          <ActionButton variant="danger" action={() => post("reject-spec", { reason, next: "cancel" })}>Reject: cancel run</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "waiting_plan_approval" && plan) {
    return (
      <Panel run={run} title={`Plan v${plan.version}`}>
        <GroundingSummary plan={plan} />
        <PlanSummary summary={plan.plan.summary} titles={Object.fromEntries(plan.plan.tasks.map((t) => [t.id, t.title]))} />
        <div className="overflow-hidden rounded-lg border border-line"><CoverageMatrix plan={plan} specMarkdown={spec?.content} /></div>
        {editing ? (
          <Field label="Edit plan and test plan (JSON)" htmlFor="plan-edit">
            <Textarea id="plan-edit" className="min-h-80 font-mono text-[12px]" value={planDraft} onChange={(e) => setPlanDraft(e.target.value)} />
          </Field>
        ) : null}
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Required to reject, optional to revise" />
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton variant="primary" success="Plan approved; the QC reviews the test cases next" action={() => post("approve-plan", { plan_id: plan.id, sha256: plan.sha256, checked })}>
            Approve plan v{plan.version}
          </ActionButton>
          <Button onClick={() => setEditing(!editing)}>{editing ? "Hide editor" : "Edit JSON"}</Button>
          {editing ? (
            <ActionButton action={async () => {
              let parsed: { plan: unknown; test_plan: unknown };
              try { parsed = JSON.parse(planDraft); } catch (e) { throw new Error(`invalid JSON: ${(e as Error).message}`); }
              await post("revise-plan", { plan: parsed.plan, test_plan: parsed.test_plan, reason: reason || null });
            }}>Save as new version</ActionButton>
          ) : null}
          <ActionButton variant="danger" action={() => post("reject-plan", { reason, next: "redo" })}>Reject: replan</ActionButton>
          <ActionButton variant="danger" action={() => post("reject-plan", { reason, next: "cancel" })}>Reject: cancel run</ActionButton>
          <span className="ml-auto text-xs text-muted">Approves exactly plan v{plan.version} (<Mono>{short(plan.sha256, 12)}</Mono>); a newer version is refused.</span>
        </div>
      </Panel>
    );
  }

  if (need.action === "waiting_test_approval" && tests && plan) {
    return (
      <Panel run={run} title={`Test cases v${tests.version}`}>
        <p className="m-0 text-sm text-muted">The plan is approved. Check that every acceptance criterion and edge case has the right tests before any code is written; approving creates the tasks and starts coding.</p>
        {tests.findings?.length ? (
          <div className="rounded-lg border border-warn/40 bg-warn-soft p-3 text-sm">
            <b>Grounding: {tests.findings.length} finding(s)</b>
            <ul className="mb-0 mt-1 pl-5">{tests.findings.map((f) => <li key={f}>{f}</li>)}</ul>
          </div>
        ) : null}
        <div className="max-h-[520px] overflow-y-auto rounded-lg border border-line bg-side p-4"><Markdown source={tests.content} /></div>
        {editing ? (
          <Field label="Edit the test plan (JSON)" hint="cases keep the approved plan's task ids" htmlFor="tests-edit">
            <Textarea id="tests-edit" className="min-h-80 font-mono text-[12px]" value={testsDraft} onChange={(e) => setTestsDraft(e.target.value)} />
          </Field>
        ) : null}
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Required to reject, optional to revise" />
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton variant="primary" success="Test cases approved; coding starts" action={() => post("approve-tests", { document_id: tests.id, sha256: tests.sha256, checked })}>
            Approve test cases v{tests.version}
          </ActionButton>
          <Button onClick={() => setEditing(!editing)}>{editing ? "Hide editor" : "Edit JSON"}</Button>
          {editing ? (
            <ActionButton action={async () => {
              let parsed: unknown;
              try { parsed = JSON.parse(testsDraft); } catch (e) { throw new Error(`invalid JSON: ${(e as Error).message}`); }
              await post("revise-tests", { test_plan: parsed, reason: reason || null });
            }}>Save as new version</ActionButton>
          ) : null}
          <ActionButton variant="danger" action={() => post("reject-tests", { reason, next: "redo" })}>Reject: redesign tests</ActionButton>
          <ActionButton variant="danger" action={() => post("reject-tests", { reason, next: "replan" })}>Reject: replan</ActionButton>
          <ActionButton variant="danger" action={() => post("reject-tests", { reason, next: "cancel" })}>Reject: cancel run</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "waiting_pr_approval" && run.pull_request) {
    const pr = run.pull_request;
    return (
      <Panel run={run} title={pr.simulated ? "Simulated pull request" : `Draft PR #${pr.number}`}>
        {pr.simulated ? (
          <p className="m-0 text-muted">Branch <Mono>{run.branch}</Mono> exists only in the local mirror at <Mono>{short(pr.head_sha, 10)}</Mono>. GitHub projects get a real draft PR with SDLC_DOING_MODE=cli.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <a href={pr.url ?? "#"} target="_blank" rel="noopener" className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-ink px-3.5 font-semibold text-panel hover:text-panel hover:opacity-90">
                <GitPullRequest className="size-4" aria-hidden />Open PR #{pr.number}<ExternalLink className="size-3.5" aria-hidden />
              </a>
              <span className="text-sm text-muted">CI</span><StatusBadge status={pr.ci_status} />
              <span className="text-sm text-muted">review</span><StatusBadge status={pr.review_status} />
              <span className="text-sm text-muted">head <Mono>{short(pr.head_sha)}</Mono></span>
            </div>
            <p className="m-0 text-sm text-muted">Merge it in GitHub; the platform never merges. The run completes when GitHub reports a merge of this exact head (checked every minute).</p>
          </>
        )}
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="What should change (required for request changes)" />
        <div className="flex flex-wrap gap-2">
          {pr.simulated ? <ActionButton variant="primary" action={() => post("pr-decision", { decision: "approve", checked })}>Record simulated merge</ActionButton>
            : <ActionButton variant="primary" success="Review checklist recorded; merge in GitHub" action={() => post("pr-decision", { decision: "checklist", checked })}>Record review checklist</ActionButton>}
          <ActionButton action={() => post("pr-decision", { decision: "request_changes", reason })}>Request changes</ActionButton>
          <ActionButton action={() => post("pr-decision", { decision: "rereview", reason })}>Review again</ActionButton>
          <ActionButton variant="danger" action={() => post("pr-decision", { decision: "close", reason })}>Close without merge</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "needs_human") {
    const disputed = run.tasks.some((t) => t.status === "DISPUTED");
    return (
      <Panel run={run} title="The team stopped">
        <p className="m-0 rounded-lg bg-bad-soft p-3 text-sm text-bad">{run.reason}</p>
        {disputed ? (
          <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
            <p className="m-0 text-sm text-ink-2">The Developer disputes a test. Decide who is right: let the QA Engineer update the tests (existing test lines may change and are listed in the PR), or keep the tests and send the Developer back.</p>
            <div className="flex flex-wrap gap-2">
              <ActionButton variant="primary" action={() => post("resume", { decision: "amend_tests", reason })}>Let QA update the tests</ActionButton>
              <ActionButton action={() => post("resume", { decision: "keep_tests", reason })}>Keep the tests</ActionButton>
            </div>
          </div>
        ) : null}
        <Reason value={reason} onChange={setReason} placeholder="What you checked and why you decide this (required)" />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant={disputed ? "default" : "primary"} action={() => post("resume", { decision: "resume", reason })}>Resume</ActionButton>
          <ActionButton action={() => post("resume", { decision: "replan", reason })}>Replan</ActionButton>
          <ActionButton variant="danger" action={() => post("resume", { decision: "cancel", reason })}>Cancel run</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "classify") {
    const m = meta.data;
    const value = {
      category: classification.category || m?.failure_categories[0] || "",
      stage: classification.stage || m?.failure_stages[0] || "",
      role: classification.role || m?.failure_roles[0] || "",
      note: classification.note,
    };
    return (
      <Panel run={run} title={ACTION_LABEL.classify}>
        <p className="m-0 text-sm text-muted">Required for failed runs and for runs a human had to correct.</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Category" htmlFor="c-cat"><Select id="c-cat" value={value.category} onChange={(e) => setClassification({ ...value, category: e.target.value })}>{m?.failure_categories.map((o) => <option key={o}>{o}</option>)}</Select></Field>
          <Field label="Stage" htmlFor="c-stage"><Select id="c-stage" value={value.stage} onChange={(e) => setClassification({ ...value, stage: e.target.value })}>{m?.failure_stages.map((o) => <option key={o}>{o}</option>)}</Select></Field>
          <Field label="Role whose output was wrong" htmlFor="c-role"><Select id="c-role" value={value.role} onChange={(e) => setClassification({ ...value, role: e.target.value })}>{m?.failure_roles.map((o) => <option key={o}>{o}</option>)}</Select></Field>
        </div>
        <Field label="Note" hint="required for OTHER" htmlFor="c-note"><Textarea id="c-note" value={value.note} onChange={(e) => setClassification({ ...value, note: e.target.value })} /></Field>
        <div><ActionButton variant="primary" action={() => post("classify-failure", value)}>Save classification</ActionButton></div>
      </Panel>
    );
  }
  return null;
}


/** The Solution Architect picks the architecture option the SDS designs; the reason goes into SDS section 9. */
function OptionsPanel({ run, doc, checklist, checked, post }: { run: RunDetail; doc: Doc;
  checklist: ReactNode; checked: string[]; post: (path: string, body: unknown) => Promise<void> }) {
  const ids = Array.from(doc.content.matchAll(/\| (PA\d+) [^|]*\|/g)).map((m) => m[1]);
  const options = doc.choices?.options.map((o) => o.id) ?? Array.from(new Set(ids));
  const recommended = doc.choices?.recommendation ?? /\*\*Recommendation:\*\* (PA\d+)/.exec(doc.content)?.[1] ?? options[0] ?? "";
  const [option, setOption] = useState(recommended);
  const choice = doc.choices?.options.find((o) => o.id === option);
  const [picked, setPicked] = useState<Record<string, string>>({});  // option id -> hosting variant id
  const hosting = choice ? picked[choice.id] ?? choice.recommended_hosting : "";
  const variant = choice?.hosting.find((v) => v.id === hosting);
  const [why, setWhy] = useState("");
  const [adjust, setAdjust] = useState("");
  return (
    <Panel run={run} title={`Architecture options v${doc.version}`}>
      <p className="m-0 text-sm text-muted">The Architect compared the options; you pick the one the SDS designs. Your reason goes into SDS section 9.</p>
      <div className="max-h-[460px] overflow-auto rounded-lg border border-line bg-side p-4"><Markdown source={doc.content} /></div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {options.map((o) => (
          <label key={o} className="flex items-center gap-1.5"><input type="radio" name="option" checked={option === o} onChange={() => setOption(o)} />{o}{o === recommended ? " (recommended)" : ""}</label>
        ))}
      </div>
      {choice?.hosting.length ? (
        <div className="flex flex-col gap-1.5 text-sm">
          <span className="font-semibold">Hosting of {choice.id}</span>
          <div className="flex flex-wrap items-center gap-3">
            {choice.hosting.map((v) => (
              <label key={v.id} className="flex items-center gap-1.5">
                <input type="radio" name="hosting" checked={hosting === v.id} onChange={() => setPicked({ ...picked, [choice.id]: v.id })} />
                {v.id} {v.name} <span className="text-muted">({v.platform})</span>{v.id === choice.recommended_hosting ? " (recommended)" : ""}
              </label>
            ))}
          </div>
        </div>
      ) : null}
      {checklist}
      <Field label="Adjustments to the chosen option" hint="optional: what you add or change before the SDS is written, such as another database or an added queue" htmlFor="option-adjust"><Textarea id="option-adjust" className="min-h-14" value={adjust} onChange={(e) => setAdjust(e.target.value)} placeholder="Use PostgreSQL instead of MySQL; add Redis for the event queue" /></Field>
      <Field label="Why this option" htmlFor="option-why"><Textarea id="option-why" className="min-h-16" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Against the quality targets, constraints and cost; required, also to ask for other options" /></Field>
      <div className="flex flex-wrap gap-2">
        <ActionButton variant="primary" disabled={!option || !why.trim() || (!!choice?.hosting.length && !variant)} success={`${option} chosen; the Architect writes the SDS`} action={() => post("options/choose", { document_id: doc.id, option, hosting, reason: why, checked, adjustments: adjust })}>Choose {option}{variant ? ` on ${variant.platform}` : ""}</ActionButton>
        <ActionButton variant="danger" disabled={!why.trim()} action={() => post("options/redo", { reason: why })}>Ask for other options</ActionButton>
      </div>
    </Panel>
  );
}
