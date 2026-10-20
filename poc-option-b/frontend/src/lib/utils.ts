import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const ROLE_LABEL: Record<string, string> = {
  product_owner: "Product Owner",
  tech_lead: "Tech Lead",
  qc: "QC",
  architect: "Architect",
  code_owner: "Code Owner",
  platform_admin: "Platform Admin",
  solution_architect: "Solution Architect",
  business_analyst: "Business Analyst",
  tech_lead_ai: "Tech Lead (AI)",
  qa_analyst: "QA Analyst",
  qa_engineer: "QA Engineer",
  developer: "Developer",
  validation_gate: "Validation Gate",
  code_reviewer: "Code Reviewer",
  release_engineer: "Release Engineer",
  coordinator: "Coordinator",
};

export const ACTION_LABEL: Record<string, string> = {
  needs_clarification: "Answer the Business Analyst's questions",
  answer_questions: "Questions for you",
  ingesting: "Add the sources, then start the extraction",
  clarifying: "Review the requirement inventory",
  waiting_baseline_approval: "Approve the baseline",
  waiting_options_approval: "Choose the architecture option",
  waiting_onboarding_approval: "Approve the onboarding files",
  waiting_spec_approval: "Review spec.md",
  waiting_plan_approval: "Review the plan",
  waiting_test_approval: "Review the test cases",
  waiting_pr_approval: "Review the pull request",
  needs_human: "Decide how to continue",
  start: "Start a manual stage",
  classify: "Classify what went wrong",
};

export const ACTIVE_WORK = ["RECEIVED", "PREFLIGHT", "EXTRACTING", "DESIGNING_OPTIONS", "DESIGNING_ONBOARDING", "PREPARING_ENVIRONMENT", "SPECIFYING", "PLANNING", "GROUNDING", "DESIGNING_TESTS", "READY", "CODING",
  "REVIEWING", "PR_OPEN"];
export const TERMINAL = ["COMPLETED", "CANCELLED", "FAILED", "CLOSED_UNMERGED"];

export function role(value: string | null | undefined) {
  return value ? ROLE_LABEL[value] ?? value.replace(/_/g, " ") : "";
}

export function ago(iso: string | null | undefined) {
  if (!iso) return "";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export function when(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString() : "";
}

export function duration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

export function pct(value: number | null | undefined) {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`;
}

export function short(sha: string | null | undefined, n = 7) {
  return sha ? sha.slice(0, n) : "";
}

export type Tone = "neutral" | "accent" | "good" | "warn" | "flag" | "bad" | "violet";

export function statusTone(status: string): Tone {
  const s = status.toLowerCase();
  if (["completed", "passed", "ok", "ready", "pass", "success", "approved", "succeeded"].includes(s)) return "good";
  if (["needs_human", "failed", "fail", "failure", "disputed", "unknown", "not_ready", "closed_unmerged"].includes(s)) return "bad";
  if (["warn", "plan_only", "changes_requested", "pending", "postponed"].includes(s)) return "warn";
  if (s.startsWith("waiting") || s === "needs_clarification") return "accent";
  if (["cancelled", "discarded", "none", "simulated"].includes(s)) return "neutral";
  return "violet";
}

export function humanStatus(status: string) {
  const map: Record<string, string> = {
    WAITING_SPEC_APPROVAL: "Waiting for spec approval",
    WAITING_ONBOARDING_APPROVAL: "Waiting for onboarding approval",
    WAITING_PLAN_APPROVAL: "Waiting for plan approval",
    WAITING_TEST_APPROVAL: "Waiting for test approval",
    WAITING_PR_APPROVAL: "Waiting for merge",
    NEEDS_CLARIFICATION: "Waiting for answers",
    NEEDS_HUMAN: "Needs a human",
    CLOSED_UNMERGED: "Closed unmerged",
  };
  return map[status] ?? status.charAt(0) + status.slice(1).toLowerCase().replace(/_/g, " ");
}

/** Acceptance criteria and edge cases of a spec: `- **AC-01 — Name:**` (company template) or older `- AC-1: text`. */
export function parseCriteria(markdown: string | undefined): { id: string; text: string }[] {
  const body = (markdown ?? "").split("### Business Analyst notes")[0];
  const current = [...body.matchAll(/^- \*\*((?:AC|EC)-\d+) — (.+?):\*\*/gm)].map((m) => ({ id: m[1], text: m[2] }));
  return current.length ? current : [...body.matchAll(/^- (AC-\d+):\s*(.+)$/gm)].map((m) => ({ id: m[1], text: m[2] }));
}
