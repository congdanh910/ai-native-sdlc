// Typed access to the controller API. Every write carries X-SDLC-Client: web (the API refuses writes without it).

export class ApiError extends Error {}

export async function api<T = unknown>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: { "Content-Type": "application/json", "X-SDLC-Client": "web" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && (data as { detail?: unknown }).detail;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail ?? response.statusText));
  }
  return data as T;
}

export type Waiting = { role: string; action: string; stage?: string | null } | null;

export interface RunSummary {
  id: string;
  title: string;
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
  approvals: { stage: string; decision: string; actor: string; role: string; reason: string | null; created_at: string }[];
  pull_request: {
    url: string | null; number: number | null; head_sha: string | null; ci_status: string; review_status: string;
    merged_sha: string | null; simulated: boolean;
  } | null;
  executions: { kind: string; role: string; status: string; attempts: number; engine: string | null; error: string | null }[];
  task_steps: { task: string | null; steps: Step[] }[];
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
