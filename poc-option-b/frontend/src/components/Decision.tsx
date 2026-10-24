import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, GitPullRequest } from "lucide-react";
import { api, projectDoc, type Doc, type GateFix, type Meta, type RunDetail } from "../lib/api";
import { ACTION_LABEL, role, short } from "../lib/utils";
import { CoverageMatrix, GroundingSummary } from "./Coverage";
import { GateChecklist } from "./GateChecklist";
import { PlanSummary } from "./PlanSummary";
import { RevisionSummary } from "./Revision";
import { PreviewBox } from "./PreviewBox";
import { AcceptanceReport } from "./AcceptanceReport";
import { ActionButton, Badge, Button, Card, Field, Input, Markdown, Mono, Select, StatusBadge, Textarea } from "./ui";

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
  const [waitFor, setWaitFor] = useState<string[]>([]);
  const [allowFiles, setAllowFiles] = useState("");
  const [decideMyself, setDecideMyself] = useState(false);
  const onFix = async (fix: GateFix, mode: "use" | "do" | "amend") => {
    if (mode === "use") {
      setReason(fix.reason);
      document.getElementById("decision-reason")?.focus();
      return;
    }
    if (mode === "amend" && fix.amend) return post("amend-plan", { ...fix.amend, reason: fix.reason });
    if (fix.action === "rewrite_spec") return post("reject-spec", { reason: fix.reason, next: "redo" });
    if (fix.action === "revise_onboarding") return post("onboarding/redo", { reason: fix.reason });
    if (fix.action === "replan" && need?.action === "waiting_plan_approval") return post("reject-plan", { reason: fix.reason, next: "redo" });
    return post("reject-tests", { reason: fix.reason, next: fix.action === "replan" ? "replan" : "redo" });
  };
  const checklist = run.checklist ? <GateChecklist checklist={run.checklist} checked={checked} onChange={setChecked} onFix={onFix} /> : null;
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

  if (need.action === "waiting_acceptance_approval") {
    const report = [...run.documents].reverse().find((d) => d.kind === "acceptance");
    if (!report) return null;
    const openFindings = run.open_findings?.length ?? 0;
    const failedTests = ((report as typeof report & { acceptance?: { cases: { status: string }[] } }).acceptance?.cases ?? []).filter((c) => c.status !== "passed" && c.status !== "skipped").length;
    return (
      <Panel run={run} title={`Acceptance ${report.version > 1 ? `v${report.version}` : ""}`.trim()}>
        <p className="m-0 text-sm text-muted">The QA Engineer's end-to-end tests ran against the story's preview. Try the story yourself with the demo accounts, check the evidence of every criterion, then accept it (the pull request opens) or send the defects back to the Developer.</p>
        <PreviewBox run={run} onChange={onDone} />
        <AcceptanceReport run={run} doc={report} />
        {run.regression_triage ? <RegressionTriage triage={run.regression_triage} onUse={setReason} /> : null}
        <OpenFindings run={run} />
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Optional: what you did, what you saw, what you expected. The failed tests, the Code Reviewer's findings and the application's errors are sent for you" />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant="primary" success="Accepted; opening the PR" action={() => post("acceptance", { decision: "accept", document_id: report.id, checked })}>Accept the story</ActionButton>
          <ActionButton variant="danger" disabled={!reason.trim() && !failedTests && !openFindings} title="The platform writes the notes from the failed tests, the Code Reviewer's findings and the errors the application logged; your text is added first" action={() => post("acceptance", { decision: "reject", reason })}>{reason.trim() ? "Send defects to the Developer" : failedTests ? `Send the ${failedTests} failed test(s) to the Developer` : `Send the ${openFindings} review finding(s) to the Developer`}</ActionButton>
          <ActionButton action={() => post("acceptance", { decision: "retest", reason })}>Run the tests again</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "waiting_onboarding_approval") {
    const onboarding = [...run.documents].reverse().find((d) => d.kind === "onboarding");
    if (!onboarding) return null;
    return (
      <Panel run={run} title={`Onboarding v${onboarding.version}`}>
        <p className="m-0 text-sm text-muted">The Architect rewrote the onboarding files from SDS.md: AGENTS.md, REVIEW.md, the technical part of .sdlc/project.yaml (paths, validation, setup, environment) and the validation environment. Each file is shown against the default branch. Approving commits them to the run's branch; the platform then builds the validation image and starts its services, and on success the run builds the walking skeleton with these files. A failing environment goes back to the Architect with the log.</p>
        <RevisionSummary runId={run.id} doc={onboarding} />
        <div className="max-h-[560px] overflow-y-auto rounded-lg border border-line bg-side p-4"><Markdown source={onboarding.content} /></div>
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Required to ask for changes" />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant="primary" success="Onboarding approved; building the environment" action={() => post("onboarding/approve", { document_id: onboarding.id, checked })}>Approve onboarding v{onboarding.version}</ActionButton>
          <ActionButton variant="danger" disabled={!reason.trim()} action={() => post("onboarding/redo", { reason })}>Ask for changes</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "waiting_options_approval") {
    const doc = [...run.documents].reverse().find((d) => d.kind === "options");
    if (!doc) return null;
    return <OptionsPanel run={run} doc={doc} checklist={checklist} checked={checked} post={post} />;
  }

  if (need.action === "waiting_ui_approval") {
    const doc = [...run.documents].reverse().find((d) => d.kind === "ui_concepts");
    if (!doc?.ui) return null;
    return <UiConceptPanel run={run} doc={doc} checklist={checklist} checked={checked} post={post} />;
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
          {run.kind === "foundation" ? <ActionButton disabled={!reason.trim()} title="The onboarding files miss something this stage needs (a path, a command): the Architect revises them, you approve them again, and the run comes back here" action={() => post("onboarding/reopen", { reason })}>Back to onboarding</ActionButton> : null}
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
        {run.overlaps?.length ? <OverlapCard overlaps={run.overlaps} waitFor={waitFor} onChange={setWaitFor} /> : null}
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder="Required to reject, optional to revise" />
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton variant="primary" success={waitFor.length ? "Plan approved; the run codes after the runs it waits for finish" : "Plan approved; the QC reviews the test cases next"} action={() => post("approve-plan", { plan_id: plan.id, sha256: plan.sha256, checked, wait_for: waitFor })}>
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
          {run.kind === "foundation" ? <ActionButton disabled={!reason.trim()} title="The onboarding files miss something this stage needs (a path, a command): the Architect revises them, you approve them again, and the run comes back here" action={() => post("onboarding/reopen", { reason })}>Back to onboarding</ActionButton> : null}
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
          {run.kind === "foundation" ? <ActionButton disabled={!reason.trim()} title="The onboarding files miss something this stage needs (a path, a command): the Architect revises them, you approve them again, and the run comes back here" action={() => post("onboarding/reopen", { reason })}>Back to onboarding</ActionButton> : null}
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
        <OpenFindings run={run} />
        {checklist}
        <Reason value={reason} onChange={setReason} placeholder={run.open_findings?.length ? "Optional: what else should change; the open review findings are sent with it" : "What should change (required for request changes)"} />
        <div className="flex flex-wrap gap-2">
          {pr.simulated ? <ActionButton variant="primary" action={() => post("pr-decision", { decision: "approve", checked })}>Record simulated merge</ActionButton>
            : <ActionButton variant="primary" success="Review checklist recorded; merge in GitHub" action={() => post("pr-decision", { decision: "checklist", checked })}>Record review checklist</ActionButton>}
          <ActionButton action={() => post("pr-decision", { decision: "request_changes", reason })}>{!reason.trim() && run.open_findings?.length ? `Send the ${run.open_findings.length} review finding(s) to the Developer` : "Request changes"}</ActionButton>
          <ActionButton action={() => post("pr-decision", { decision: "rereview", reason })}>Review again</ActionButton>
          <ActionButton variant="danger" action={() => post("pr-decision", { decision: "close", reason })}>Close without merge</ActionButton>
        </div>
      </Panel>
    );
  }

  if (need.action === "ai_deciding" && !decideMyself) {
    return (
      <Panel run={run} title="The Code Reviewer is settling a technical dispute">
        <p className="m-0 rounded-lg bg-sunken p-3 text-sm text-ink-2">{run.reason}</p>
        <p className="m-0 text-sm text-muted">This is a disagreement between the AI roles (a test, a file, a layer). The Code Reviewer reads the tests and the code and decides on its own when it is sure and nothing touches what a criterion means, the SDS, security or the design (1–3 minutes). You are asked only if it cannot; its decision then shows under Decisions taken by the AI.</p>
        <div><Button onClick={() => setDecideMyself(true)}>Decide it myself instead</Button></div>
      </Panel>
    );
  }

  if (need.action === "needs_human" || need.action === "ai_deciding") {
    const disputed = run.tasks.some((t) => t.status === "DISPUTED");
    return (
      <Panel run={run} title="The team stopped">
        <p className="m-0 rounded-lg bg-bad-soft p-3 text-sm text-bad">{run.reason}</p>
        {run.defect_check ? <DefectCheckCard run={run} reason={reason} onUse={setReason} post={post} /> : null}
        {run.advice ? <AdviceCard run={run} onUse={setReason} /> : null}
        {run.already_built ? <AlreadyBuilt evidence={run.already_built} /> : null}
        {disputed ? (
          <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
            <p className="m-0 text-sm text-ink-2">{(run.reason ?? "").includes("QA Engineer finds the decision") ? "The QA Engineer could not follow your decision as written. Decide again with a reason that says exactly what the tests must check (which test, which value), or keep the tests." : "The Developer disputes a test. Decide who is right: let the QA Engineer update the tests (existing test lines may change and are listed in the PR), or keep the tests and send the Developer back."}</p>
            <div className="flex flex-wrap gap-2">
              <ActionButton variant="primary" action={() => post("resume", { decision: "amend_tests", reason })}>Let QA update the tests</ActionButton>
              <ActionButton action={() => post("resume", { decision: "keep_tests", reason })}>Keep the tests</ActionButton>
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2 text-sm">
              <span className="text-ink-2">The work needs a file outside the task's files?</span>
              <Input className="min-w-[280px] flex-1" placeholder="backend/app/persistence/departments.py, …" value={allowFiles}
                aria-label="Files the task may change too" onChange={(e) => setAllowFiles(e.target.value)} />
              <ActionButton disabled={!allowFiles.trim() || !reason.trim()} title="The task may change these files too (inside the allowed paths, never a test file); the tests stand and the Developer tries again"
                action={() => post("resume", { decision: "allow_files", reason, files: allowFiles.split(/[\s,]+/).filter(Boolean) })}>Allow these files, keep the tests</ActionButton>
            </div>
          </div>
        ) : null}
        <Reason value={reason} onChange={setReason} placeholder="What you checked and why you decide this (required)" />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant={disputed ? "default" : "primary"} action={() => post("resume", { decision: "resume", reason })}>Resume</ActionButton>
          <ActionButton action={() => post("resume", { decision: "replan", reason })}>Replan</ActionButton>
          {(run.reason ?? "").includes("already pass before the implementation") ? <ActionButton disabled={!reason.trim()} title="The code already does what these tests check (for example the run's earlier plan built it): keep the tests as regression guards, skip the Developer, and let the validation decide" action={() => post("resume", { decision: "already_built", reason })}>Already built: keep tests as guards</ActionButton> : null}
          {run.kind === "foundation" ? <ActionButton disabled={!reason.trim()} title="The onboarding files are the cause (the preview's compose file, a command, a path): the Architect revises them, you approve them again, and the Tech Lead plans again on the approved spec" action={() => post("onboarding/reopen", { reason })}>Back to onboarding</ActionButton> : null}
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
  // Hosting the brief did not state is the Architect's assumption: nothing is preselected and the SA says why.
  const assumed = doc.choices?.hosting_basis === "assumed";
  const hosting = choice ? picked[choice.id] ?? (assumed ? "" : choice.recommended_hosting) : "";
  const variant = choice?.hosting.find((v) => v.id === hosting);
  const [hostingWhy, setHostingWhy] = useState("");
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
          {assumed ? (
            <p className="m-0 rounded-md border border-warn/40 bg-warn-soft p-2 text-warn">The brief does not state the deployment environment, so the recommended hosting is the Architect's assumption. Choose it knowingly and say why below; the questions above say what would decide it.</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            {choice.hosting.map((v) => (
              <label key={v.id} className="flex items-center gap-1.5">
                <input type="radio" name="hosting" checked={hosting === v.id} onChange={() => setPicked({ ...picked, [choice.id]: v.id })} />
                {v.id} {v.name} <span className="text-muted">({v.platform})</span>{v.id === choice.recommended_hosting ? " (recommended)" : ""}
              </label>
            ))}
          </div>
          {assumed ? (
            <Field label="Why this hosting" htmlFor="hosting-why"><Textarea id="hosting-why" className="min-h-12" value={hostingWhy} onChange={(e) => setHostingWhy(e.target.value)} placeholder="The customer already runs its HR system on Azure; biometric data must stay in Vietnam" /></Field>
          ) : null}
        </div>
      ) : null}
      {checklist}
      <Field label="Adjustments to the chosen option" hint="optional: what you add or change before the SDS is written, such as another database or an added queue" htmlFor="option-adjust"><Textarea id="option-adjust" className="min-h-14" value={adjust} onChange={(e) => setAdjust(e.target.value)} placeholder="Use PostgreSQL instead of MySQL; add Redis for the event queue" /></Field>
      <Field label="Why this option" htmlFor="option-why"><Textarea id="option-why" className="min-h-16" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Against the quality targets, constraints and cost; required, also to ask for other options" /></Field>
      <div className="flex flex-wrap gap-2">
        <ActionButton variant="primary" disabled={!option || !why.trim() || (!!choice?.hosting.length && (!variant || (assumed && !hostingWhy.trim())))} success={`${option} chosen; the Architect writes the SDS`} action={() => post("options/choose", { document_id: doc.id, option, hosting, hosting_reason: hostingWhy, reason: why, checked, adjustments: adjust })}>Choose {option}{variant ? ` on ${variant.platform}` : ""}</ActionButton>
        <ActionButton variant="danger" disabled={!why.trim()} action={() => post("options/redo", { reason: why })}>Ask for other options</ActionButton>
      </div>
    </Panel>
  );
}


/** What the Tech Lead checks before ruling a task already built: the earlier commits that changed its files, the
 * tests the QA Engineer added, and its note. */
function AlreadyBuilt({ evidence }: { evidence: NonNullable<RunDetail["already_built"]> }) {
  const mine = new Set(evidence.files);
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3 text-sm">
      <p className="m-0 text-ink-2">
        <b>{evidence.task}</b> {evidence.title}: its new tests pass on the code as it is. Either the code already does
        this (keep the tests as guards), or the tests check too little (Replan, or ask for stronger tests).
      </p>
      <div>
        <b>Earlier commits of this run that changed the task's files</b>
        {evidence.earlier_commits.length ? (
          <ul className="m-0 mt-1 pl-5">
            {evidence.earlier_commits.map((c) => (
              <li key={c.sha}><Mono>{c.sha.slice(0, 8)}</Mono> {c.subject}
                <span className="text-xs text-muted"> ({c.files.filter((f) => mine.has(f)).join(", ")})</span></li>
            ))}
          </ul>
        ) : <p className="m-0 mt-1 text-muted">None: nothing earlier built these files, so suspect the tests.</p>}
      </div>
      <div>
        <b>Tests the QA Engineer added ({evidence.tests.length})</b>
        <ul className="m-0 mt-1 pl-5">
          {evidence.tests.map((t) => <li key={`${t.file}:${t.name}`}><Mono>{t.name}</Mono> <span className="text-xs text-muted">{t.file}</span></li>)}
        </ul>
      </div>
      {evidence.qa_note ? (
        <div><b>QA Engineer's note</b><p className="m-0 mt-1 whitespace-pre-wrap text-ink-2">{evidence.qa_note}</p></div>
      ) : null}
      {evidence.red_check ? <p className="m-0 text-xs text-muted">Red check: {evidence.red_check}</p> : null}
    </div>
  );
}


const DECISION_LABEL: Record<string, string> = {
  amend_tests: "Let QA update the tests", keep_tests: "Keep the tests", allow_files: "Allow these files, keep the tests", already_built: "Already built: keep tests as guards",
  replan: "Replan", resume: "Resume", back_to_onboarding: "Back to onboarding",
};

/** The Code Reviewer's brief for the Tech Lead on a stopped task: the situation, each side with its evidence, and a
 * recommended decision with a reason ready to use. The Tech Lead decides. */
function AdviceCard({ run, onUse }: { run: RunDetail; onUse: (reason: string) => void }) {
  const brief = run.advice!;
  const ask = async () => { await api(`/api/runs/${run.id}/advice`, { method: "POST" }); };
  if (!brief.advice) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-3 text-sm text-muted">
        {brief.pending ? "The Code Reviewer is reading the tests and the code to brief you (1–3 minutes)…"
          : brief.error ? `The brief could not be written: ${brief.error.slice(0, 200)}` : "No brief for this stop yet."}
        {brief.pending ? null : <ActionButton success="Asked the Code Reviewer for a brief" action={ask}>Ask for a brief</ActionButton>}
      </div>
    );
  }
  const a = brief.advice;
  const tone = a.confidence === "high" ? "good" : a.confidence === "medium" ? "warn" : "bad";
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-accent/40 bg-accent-soft/30 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <b>Brief for the Tech Lead</b>
        <span className="text-xs text-muted">Code Reviewer{brief.engine ? `, ${brief.engine}` : ""}; advisory, you decide</span>
      </div>
      {brief.not_decided ? <p className="m-0 rounded bg-warn-soft p-2 text-xs"><b>Why the team did not settle this on its own:</b> {brief.not_decided}</p> : null}
      <p className="m-0">{a.situation}</p>
      <div className="flex flex-col gap-2">
        {a.sides.map((side, i) => (
          <div key={i} className="rounded border border-line bg-panel p-2">
            <b>{side.party}</b>: {side.claim}
            {side.evidence.length ? (
              <ul className="m-0 mt-1 pl-5 text-xs text-ink-2">{side.evidence.map((e, j) => <li key={j}><Mono>{e}</Mono></li>)}</ul>
            ) : null}
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1 rounded border border-line bg-panel p-2">
        <div className="flex flex-wrap items-center gap-2">
          <span>Recommended: <b>{DECISION_LABEL[a.recommendation] ?? a.recommendation}</b>{a.files?.length ? <> · <Mono>{a.files.join(", ")}</Mono></> : null}</span>
          <Badge tone={tone}>{a.confidence} confidence</Badge>
        </div>
        <p className="m-0 whitespace-pre-wrap text-ink-2">{a.reason}</p>
        {a.risk ? <p className="m-0 text-xs text-muted">If this is wrong: {a.risk}</p> : null}
        {a.checks.length ? (
          <div className="text-xs"><b>Check before deciding</b><ul className="m-0 pl-5">{a.checks.map((c, i) => <li key={i}>{c}</li>)}</ul></div>
        ) : null}
        <div className="flex flex-wrap gap-2 pt-1">
          <Button onClick={() => onUse(a.reason)}>Use this reason</Button>
          <ActionButton success="Asked the Code Reviewer for a new brief" action={ask}>Ask again</ActionButton>
        </div>
      </div>
    </div>
  );
}


/** Open runs that change files this plan changes. Ticked runs finish (merge, or close) before this one codes;
 * otherwise both code at once and the second PR meets the first one's changes when it updates with main. */
function OverlapCard({ overlaps, waitFor, onChange }: {
  overlaps: NonNullable<RunDetail["overlaps"]>; waitFor: string[]; onChange: (ids: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-warn bg-warn-soft p-3 text-sm">
      <div><b>Other open runs change the same files</b> <span className="text-xs text-muted">tick a run to code after it finishes; leave unticked to code now</span></div>
      {overlaps.map((o) => (
        <label key={o.run} className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={waitFor.includes(o.run)}
            onChange={(e) => onChange(e.target.checked ? [...waitFor, o.run] : waitFor.filter((r) => r !== o.run))} />
          <span>
            Wait for <a href={`#/runs/${o.run}`}>{o.title}</a> <span className="text-xs text-muted">({o.status.replace(/_/g, " ").toLowerCase()})</span>
            <span className="block text-xs text-ink-2">{o.files.map((f) => <Mono key={f} className="mr-2">{f}</Mono>)}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

const VERDICT_TONE = { consistent: "good", conflict: "bad", not_covered: "neutral", not_actionable: "warn" } as const;

/** The SDS check of the QC's or Code Owner's defect notes: each point with its verdict and evidence. The person sends
 * the notes as written (their call) or revised (checked again). */
function DefectCheckCard({ run, reason, onUse, post }: {
  run: RunDetail; reason: string; onUse: (r: string) => void; post: (path: string, body: object) => Promise<void>;
}) {
  const { defects, check } = run.defect_check!;
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3 text-sm">
      <div><b>Your notes against the SDS</b> <span className="text-xs text-muted">Code Reviewer; the team follows your notes over the approved test cases, so a conflict would be built as written</span></div>
      <p className="m-0">{check.summary}</p>
      {check.points.map((p, i) => (
        <div key={i} className="rounded border border-line bg-panel p-2">
          <div className="flex flex-wrap items-center gap-2"><Badge tone={VERDICT_TONE[p.verdict] ?? "neutral"}>{p.verdict.replace("_", " ")}</Badge> {p.point}</div>
          {p.note ? <p className="m-0 mt-1 text-ink-2">{p.note}</p> : null}
          {p.evidence.length ? <ul className="m-0 mt-1 pl-5 text-xs text-ink-2">{p.evidence.map((e, j) => <li key={j}><Mono>{e}</Mono></li>)}</ul> : null}
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => onUse(defects.reason)}>Edit my notes</Button>
        <ActionButton disabled={!reason.trim()} title="Check the revised notes in the box against the SDS again, then give them to the fix task" action={() => post("resume", { decision: "defects_revised", reason })}>Send revised notes</ActionButton>
        <ActionButton disabled={!reason.trim()} title="Give the notes as first written to the fix task; the reason box records why the design does not apply" action={() => post("resume", { decision: "defects_as_written", reason })}>Send as written</ActionButton>
        {defects.who === "QC (acceptance)" ? (
          <ActionButton title="The notes are about the acceptance tests, not the code: the QA Engineer rewrites the tests with them (the box, or the notes as first written) and they run again; no code changes" action={() => post("resume", { decision: "defects_retest", reason: reason.trim() || defects.reason })}>Run the acceptance tests again instead</ActionButton>
        ) : null}
      </div>
    </div>
  );
}


/** The UI Concept gate: each concept's palette, contrast checks and prototype screens (rendered by the controller,
 * shown in a sandboxed frame at the width of each target device); the Product Owner picks one with a reason. */
function UiConceptPanel({ run, doc, checklist, checked, post }: {
  run: RunDetail; doc: Doc; checklist: ReactNode; checked: string[]; post: (path: string, body: object) => Promise<void>;
}) {
  const ui = doc.ui!;
  const [chosen, setChosen] = useState(ui.concepts[0]?.id ?? "");
  const [device, setDevice] = useState(ui.devices[0] ?? "desktop");
  const [reason, setReason] = useState("");
  const [adjustments, setAdjustments] = useState("");
  const widths: Record<string, number> = { desktop: 1280, tablet: 820, mobile: 390, kiosk: 1080 };
  const concept = ui.concepts.find((c) => c.id === chosen) ?? ui.concepts[0];
  return (
    <Panel run={run} title={`UI concepts v${doc.version}`}>
      <p className="m-0 text-sm text-muted">Pick how the product looks before the SDS is written. The chosen concept's guide, design tokens and prototype screens go into docs/ui/ with the SDS pull request; the foundation builds its design system from them.{ui.brand.logo_name || ui.brand.colors?.length ? " The customer's brand is applied to every concept." : ""}</p>
      {ui.notes ? <p className="m-0 text-sm">{ui.notes}</p> : null}
      <div className="flex flex-wrap gap-2">
        {ui.concepts.map((c) => (
          <button key={c.id} type="button" onClick={() => setChosen(c.id)}
            className={`flex min-w-52 flex-col gap-1 rounded-lg border p-3 text-left text-sm ${c.id === chosen ? "border-accent bg-accent-soft/40" : "border-line bg-panel"}`}>
            <b>{c.name}</b>
            <span className="text-xs text-muted">{c.layout} · {c.density}{c.preset ? ` · from ${c.preset}` : ""}</span>
            <span className="flex gap-1">{["primary", "accent", "background", "surface", "text"].map((k) => (
              <span key={k} title={`${k} ${c.colors[k]}`} className="size-5 rounded border border-line" style={{ background: c.colors[k] }} />))}</span>
            <Badge tone={c.checks.every((x) => x.ok) ? "good" : "warn"}>{c.checks.every((x) => x.ok) ? `contrast ${ui.accessibility} ok` : `${c.checks.filter((x) => !x.ok).length} contrast issue(s)`}</Badge>
          </button>
        ))}
      </div>
      {concept ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm">{concept.rationale}</p>
          {concept.checks.some((x) => !x.ok) ? (
            <p className="m-0 text-xs text-bad">Below {ui.accessibility}: {concept.checks.filter((x) => !x.ok).map((x) => `${x.pair} ${x.ratio}:1 (needs ${x.need})`).join("; ")}</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">Device</span>
            {Object.keys(widths).filter((d) => ui.devices.includes(d) || d === "desktop").map((d) => (
              <Button key={d} variant={d === device ? "primary" : "default"} onClick={() => setDevice(d)}>{d}</Button>))}
            <span className="text-muted">Font: {concept.font}</span>
          </div>
          <div className="flex flex-col gap-3">
            {concept.screens.map((s) => {
              const src = `/api/runs/${run.id}/ui/${doc.version}/${concept.id}/${s.slug}?device=${device}`;
              return (
                <div key={s.slug} className="rounded-lg border border-line">
                  <div className="flex items-center gap-2 border-b border-line px-3 py-1 text-xs">
                    <b>{s.name}</b>{s.role ? <span className="text-muted">{s.role}</span> : null}
                    <a className="ml-auto text-accent-ink" href={src} target="_blank" rel="noreferrer">Open</a>
                  </div>
                  <div className="overflow-x-auto bg-side p-2">
                    <iframe title={`${concept.name}: ${s.name}`} src={src} sandbox="" loading="lazy"
                      style={{ width: widths[device] ?? 1280, height: device === "mobile" ? 700 : 520, border: 0, background: "white" }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {checklist}
      <Field label="Why this concept" htmlFor="ui-reason"><Textarea id="ui-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Required: who it suits, why not the others; or what the concepts miss, to ask for others" /></Field>
      <Field label="Adjustments (optional)" htmlFor="ui-adjust"><Textarea id="ui-adjust" value={adjustments} onChange={(e) => setAdjustments(e.target.value)} placeholder="For example: a warmer accent, denser tables" /></Field>
      <div className="flex flex-wrap gap-2">
        <ActionButton variant="primary" disabled={!reason.trim() || !concept} success="Concept chosen; the Architect writes the SDS" action={() => post("ui/choose", { document_id: doc.id, concept: concept?.id, reason, adjustments, checked })}>Choose {concept?.name}</ActionButton>
        <ActionButton variant="danger" disabled={!reason.trim()} action={() => post("ui/redo", { reason })}>Ask for other concepts</ActionButton>
      </div>
    </Panel>
  );
}


function OpenFindings({ run }: { run: RunDetail }) {
  const findings = run.open_findings ?? [];
  if (!findings.length) return null;
  return (
    <div className="rounded-lg border border-line p-3 text-sm">
      <div className="font-semibold">Important review findings no fix took yet</div>
      <p className="m-0 mt-1 text-xs text-muted">The Code Reviewer found them after the team's automatic fix rounds were used. Send them to the Developer with one click (the button below), or accept knowing they stay.</p>
      <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
        {findings.map((f, i) => (
          <li key={i} className="rounded border border-line p-2"><Badge tone="bad">important</Badge> {f.text}{f.file ? <> <Mono>{f.file}</Mono></> : null}</li>
        ))}
      </ul>
    </div>
  );
}

function RegressionTriage({ triage, onUse }: { triage: NonNullable<RunDetail["regression_triage"]>; onUse: (note: string) => void }) {
  return (
    <div className="rounded-lg border border-line p-3 text-sm">
      <div className="font-semibold">Earlier stories' tests that failed, judged by the Code Reviewer <span className="text-xs font-normal text-muted">({triage.confidence} confidence)</span></div>
      {triage.summary ? <p className="m-0 mt-1 text-ink-2">{triage.summary}</p> : null}
      <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
        {triage.tests.map((t) => (
          <li key={t.test} className="rounded border border-line p-2">
            <div className="flex flex-wrap items-center gap-2"><Badge tone={t.verdict === "outdated" ? "accent" : "bad"}>{t.verdict === "outdated" ? "test outdated by this story" : "broken by this story"}</Badge><span>{t.test}</span></div>
            {t.change ? <p className="m-0 mt-1">{t.change}</p> : null}
            {t.evidence.length ? <ul className="m-0 mt-1 pl-4 text-xs text-muted">{t.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul> : null}
          </li>
        ))}
      </ul>
      {triage.note ? (
        <div className="mt-2 flex flex-col gap-1">
          <p className="m-0 text-xs text-muted">Note for the QA Engineer (outdated tests are updated, everything else stays):</p>
          <p className="m-0 whitespace-pre-wrap rounded bg-panel p-2">{triage.note}</p>
          <div><Button onClick={() => onUse(triage.note)}>Use this note, then Run the tests again</Button></div>
        </div>
      ) : null}
    </div>
  );
}
