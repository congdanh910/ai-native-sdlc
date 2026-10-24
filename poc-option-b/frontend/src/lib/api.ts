// Typed access to the controller API. Every write carries X-SDLC-Client: web (the API refuses writes without it).

/** An API error with a readable message and, for invalid input, one message per field (keyed by field name). */
export class ApiError extends Error {
  constructor(message: string, readonly fields: Record<string, string> = {}) {
    super(message);
  }
}

const FIELD_LABEL: Record<string, string> = {
  title: "Title", problem: "Problem", outcome: "Desired outcome", users_and_systems: "Affected users and systems",
  constraints: "Constraints", open_questions: "Open questions", project: "Project", key: "Key", repo_url: "Repository",
  branch: "Branch", reason: "Reason", content: "Content", note: "Note", idempotency_key: "Request key",
};

interface PydanticIssue { type: string; loc: (string | number)[]; msg: string; ctx?: Record<string, unknown> }

function describeIssue(issue: PydanticIssue): [string, string] {
  const field = String(issue.loc[issue.loc.length - 1] ?? "");
  const label = FIELD_LABEL[field] ?? field.replace(/_/g, " ");
  const min = issue.ctx?.min_length as number | undefined;
  let text: string;
  if (issue.type === "string_too_short" && min === 1) text = `${label} is required`;
  else if (issue.type === "string_too_short") text = `${label} needs at least ${min} characters`;
  else if (issue.type === "string_too_long") text = `${label} is too long (at most ${issue.ctx?.max_length} characters)`;
  else if (issue.type === "missing") text = `${label} is required`;
  else text = `${label}: ${issue.msg.charAt(0).toLowerCase()}${issue.msg.slice(1)}`;
  return [field, text];
}

/** FastAPI returns either a string detail or a list of Pydantic issues; both become readable text. */
export function describeError(detail: unknown, fallback: string): ApiError {
  if (typeof detail === "string") return new ApiError(detail);
  if (Array.isArray(detail) && detail.every((d) => d && typeof d === "object" && "loc" in d)) {
    const pairs = (detail as PydanticIssue[]).map(describeIssue);
    return new ApiError(pairs.map(([, text]) => text).join(". "), Object.fromEntries(pairs));
  }
  return new ApiError(detail ? JSON.stringify(detail) : fallback);
}

export async function api<T = unknown>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers: { "Content-Type": "application/json", "X-SDLC-Client": "web" },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError("The control room cannot reach the controller. Is ./sdlc up running?");
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw describeError(data && (data as { detail?: unknown }).detail, response.statusText);
  return data as T;
}

/** Upload a file as the raw request body (the controller takes no multipart). */
export async function uploadFile<T = unknown>(path: string, file: File): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/octet-stream", "X-SDLC-Client": "web" }, body: file });
  } catch {
    throw new ApiError("The control room cannot reach the controller. Is ./sdlc up running?");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw describeError(data && (data as { detail?: unknown }).detail, response.statusText);
  return data as T;
}

export interface SourceFile {
  id: string; filename: string; kind: string; sha256: string; size: number; pages: number | null;
  has_text: boolean; uploaded_by: string; created_at: string; purpose: "requirements" | "minutes";
}

export interface Question {
  id: string; number: string; kind: string; question: string; why: string | null; suggestion: string | null;
  items: string[]; citations: Citation[]; blocking: boolean; owner_role: string;
  status: "open" | "answered" | "deferred" | "withdrawn"; status_reason: string | null; answer: string | null;
  answered_by: string | null; round: number | null;
}
export interface DiscoveryDecision {
  number: number; id: string; text: string; basis: "answer" | "minutes" | "direct"; question: string | null; source: string | null;
  location: string | null; items: string[]; role: string; status: "proposed" | "accepted" | "rejected";
  proposed_by: string; decided_by: string | null; reason: string | null; effect: string | null; new_text: string | null;
}

export type Waiting = { role: string; action: string; stage?: string | null } | null;

export interface RunSummary {
  id: string;
  title: string;
  kind: "feature" | "srs" | "sds" | "discovery" | "foundation";
  project: string;
  status: string;
  version: number;
  reason: string | null;
  waiting: Waiting;
  experiment: boolean;
  updated_at: string;
  created_at: string;
}

export interface InboxItem extends RunSummary {
  role: string;
  action: string;
  stage: string | null;
  role_name?: string;  // discovery "Questions for you": the project's role name
  questions?: number;
}

export interface Doc {
  id: string;
  kind: string;
  version: number;
  sha256: string;
  author_kind: string;
  author: string;
  content: string;
  repo_path: string | null;
  commit_sha: string | null;
  created_at: string;
  findings?: string[];
  revision?: Revision | null;
  choices?: OptionChoices;
  ui?: UiConcepts;  // kind "ui_concepts": the concepts the Product Owner picks from  // kind "options": what the Solution Architect picks from  // a rewrite after the reviewer's feedback: what they asked and how it was handled
  inventory?: Inventory;  // the requirement inventory of a discovery, for kind "inventory"
}

export interface PreviewState {
  status: "starting" | "running" | "stopping" | "stopped" | "failed" | "unavailable";
  url?: string; port?: number; started_at?: string; expires_at?: string; detail?: string;
  accounts: { role: string; username: string; password: string; note: string }[];
  available: boolean;  // the run has a branch to preview
  missing?: string[];  // build folders of the compose file the branch does not have yet
  locked?: string | null;  // why people cannot start or stop it now (the team is changing the code, acceptance runs on it)
}
export interface UiConcepts {
  notes: string; devices: string[]; accessibility: string;
  brand: { colors?: string[]; fonts?: string[]; figma_url?: string; logo_name?: string };
  concepts: {
    id: string; name: string; preset: string; layout: string; density: string; rationale: string;
    colors: Record<string, string>; font: string;
    checks: { pair: string; ratio: number; ok: boolean; need: number }[];
    screens: { name: string; role: string; slug: string }[];
  }[];
}
export interface OptionChoices {
  recommendation: string;
  hosting_basis: "stated" | "assumed";  // assumed: the brief did not state the deployment environment
  options: { id: string; name: string; recommended_hosting: string; hosting: { id: string; name: string; platform: string }[] }[];
}
export interface FeedbackReply { feedback: string; change: string; where: string[] }
export interface Revision {
  from_version: number;
  feedback: string[];
  replies: FeedbackReply[];
  sections: { heading: string; added: number; removed: number; state: "new" | "removed" | "changed" }[];
}

export interface Citation { source: string; location: string }
export interface InventoryItem {
  id: string; type: string; name: string; text: string; parent: string; phase: string; source_ref: string;
  aliases: string[]; status: string; sources: Citation[];
}
export interface Gap { kind: string; question: string; why: string; items: string[]; sources: Citation[]; suggestion: string; blocking: boolean }
export interface Inventory {
  items: InventoryItem[]; gaps: Gap[]; coverage: Record<string, string[]>; problems: string[]; coarse_citations: number;
  parts: { source: string; covers: string }[]; sources: Record<string, string>;
}

export interface PlanTask {
  id: string;
  title: string;
  depends_on: string[];
  modify: string[];
  create: string[];
  provides: string[];
  acceptance_criteria: string[];
  validation: string[];
}

export interface TestCase {
  id: string;
  task_id: string;
  criterion: string;
  description: string;
  expected: string;
  target_file: string;
  expected_pass: boolean;
}

export interface PlanVersion {
  id: string;
  version: number;
  sha256: string;
  author_kind: string;
  author: string;
  plan: { summary: string; tasks: PlanTask[] };
  test_plan: { cases: TestCase[] };
  grounding: { status: string; findings: string[]; warnings?: string[] } | null;
  commit_sha: string | null;
  created_at: string;
}

export interface Step {
  state: string;
  role: string;
  ok: boolean;
  dispute: boolean;
  detail: string;
  executor: string | null;
  commit: string | null;
  seconds: number | null;
  files: string[];
}

/** The review checklist of the gate a run waits at (aif-review-checklist.md). */
/** The next step for a failing controller check: the decision to take and a reason written for the role that redoes the work. */
export interface GateFix {
  action: "replan" | "redesign_tests" | "rewrite_spec" | "revise_onboarding"; why: string; reason: string; finding?: string;
  amend?: { task: string; criterion: string };  // fixable at once, without a model
}
export interface GateChecklist {
  gate: string;
  controller: { id: string; text: string; ok: boolean; detail: string; fixes?: GateFix[] }[];
  ai: { id: string; text: string; status: string; note: string }[];
  human: { id: string; text: string; checked?: boolean }[];
}

export interface RunDetail extends RunSummary {
  base_commit: string | null;
  branch: string | null;
  stage_modes: Record<string, string>;
  commit_artifacts: boolean;
  human_corrected: boolean;
  failure: { category: string | null; stage: string | null; role: string | null; note: string | null };
  documents: Doc[];
  plans: PlanVersion[];
  tasks: { id: string; title: string; status: string; attempts: number; depends_on: string[] }[];
  clarifications: { id: string; question: string; blocking: boolean; answer: string | null }[];
  approvals: { stage: string; decision: string; actor: string; role: string; reason: string | null; checklist: Partial<GateChecklist>; created_at: string }[];
  checklist: GateChecklist | null;
  sources?: SourceFile[];
  roles?: { id: string; name: string }[];  // discovery: the project's discovery roles
  questions?: Question[];
  decisions?: DiscoveryDecision[];
  rounds?: { used: number; limit: number };  // extraction rounds of a discovery and discovery.limits.max_rounds
  preview?: PreviewState | null;  // null when the project has no preview in .sdlc/project.yaml
  pull_request: {
    url: string | null; number: number | null; head_sha: string | null; ci_status: string; review_status: string;
    merged_sha: string | null; simulated: boolean;
  } | null;
  executions: { id: string; kind: string; role: string; status: string; attempts: number; engine: string | null; error: string | null;
    task: string | null; started_at: string | null; finished_at: string | null }[];
  task_steps: { task: string | null; steps: Step[] }[];
  // a person's defect notes checked against the SDS, while the run waits on a conflict the check found
  defect_check?: {
    defects: { who: string; reason: string };
    check: { summary: string; points: { point: string; verdict: "consistent" | "conflict" | "not_covered" | "not_actionable"; evidence: string[]; note: string }[] };
    engine?: string | null;
  } | null;
  // the Code Reviewer's brief on the stop the run waits on (null when the run is not stopped)
  advice?: {
    pending: boolean; engine?: string | null; error?: string | null;
    not_decided?: string | null;  // why the team did not settle this stop on its own
    advice: {
      situation: string; recommendation: string; confidence: "high" | "medium" | "low"; reason: string; risk: string;
      checks: string[]; sides: { party: string; claim: string; evidence: string[] }[]; files?: string[]; needs_human?: string;
    } | null;
  } | null;
  // technical disputes the Code Reviewer settled on its own (disputes.auto)
  auto_decisions?: { task: string; decision: string; reason: string; files: string[]; confidence: string; situation: string; stop: string; at: string }[];
  regression_triage?: { tests: { test: string; verdict: "outdated" | "broken"; evidence: string[]; change: string }[]; confidence: string; note: string; summary: string } | null;
  // at the plan gate: open runs that change files this plan changes; the Tech Lead may let them finish first
  overlaps?: { run: string; title: string; status: string; files: string[] }[];
  // the last merge of the default branch into the run's branch (B3)
  main_sync?: {
    main: string; commit: string; resolved: string[]; conflicts: string[]; migrations: string[]; notes: string[];
    validation: { ok: boolean; detail: string } | null; pushed: string | null; problems: string[];
  } | null;
  // at READY: the runs this run waits for before it codes
  waiting_for?: { run: string; title: string; status: string }[];
  // after a red check found a task's new tests passing before any code: what tells code already built from weak tests
  already_built?: {
    task: string; title: string; files: string[]; qa_note: string; red_check: string;
    tests: { file: string; name: string }[];
    earlier_commits: { sha: string; subject: string; files: string[] }[];
  } | null;
}

export interface RunEvent {
  id: number;
  type: string;
  actor: string;
  role: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Meta {
  operator: string;
  team_mode: string;
  doing_mode: string;
  engines: Record<string, string>;
  discovery_engine?: string;  // the Business Analyst of new discovery runs
  roles: string[];
  failure_categories: string[];
  failure_stages: string[];
  failure_roles: string[];
}

export interface Project {
  id: string;
  key: string;
  repo_url: string;
  default_branch: string;
  readiness: string;
  report: { name: string; status: string; detail: string }[];
  checked_commit: string | null;
}

export interface Check { name: string; status: string; detail: string; hint?: string | null }

export interface EvalSummary {
  cases: number;
  planned: number;
  file_recall: number | null;
  file_precision: number | null;
  grounding_pass_v1: number | null;
  grounding_pass_final: number | null;
  mean_seconds_to_plan: number | null;
  asked_blocking_questions: number;
}

export interface EvalFingerprint {
  team_mode: string;
  model: string;
  context_char_budget: number;
  prompts: Record<string, string>;
  engines?: Record<string, string>;
}

export interface EvalCase {
  case: string;
  run_id: string;
  status: string;
  reason: string | null;
  plans: number;
  criteria: number;
  blocking_questions: string[];
  tasks?: number;
  golden_tasks?: number;
  test_cases?: number;
  seconds_to_plan?: number;
  regenerated?: boolean;
  files?: string[];
  missed_files?: string[];
  extra_files?: string[];
  grounding_v1?: string;
  grounding_final?: string;
  findings_v1?: string[];
  findings_final?: string[];
  usage?: Record<string, { calls: number; seconds: number; cost_usd: number }>;
}

export interface EvalRun {
  id: string;
  suite: string;
  status: string;
  created_at: string;
  fingerprint: EvalFingerprint;
  summary: EvalSummary | null;
  progress?: { case: string; status: string }[];
  baseline?: { id: string; created_at: string; summary: EvalSummary | null; fingerprint: EvalFingerprint } | null;
  cases?: EvalCase[];
}

export interface Suite { name: string; project: string; base_commit: string; cases: string[] }

export interface Report {
  runs: { total: number; by_status: Record<string, number>; by_project: Record<string, number>; human_corrected: number;
    failure_categories: Record<string, number> };
  roles: Record<string, Record<string, number | null>>;
  engines: Record<string, { calls: number; seconds: number; cost_usd: number }>;
  evals: { id: string; suite: string; created_at: string; summary: EvalSummary | null; engines: Record<string, string> | null }[];
}

/** Project documents written before feature runs; null for a feature run. */
export function projectDoc(run: { kind: string }): { kind: "srs" | "sds"; name: string } | null {
  if (run.kind === "srs") return { kind: "srs", name: "SRS.md" };
  if (run.kind === "sds") return { kind: "sds", name: "SDS.md" };
  return null;
}
