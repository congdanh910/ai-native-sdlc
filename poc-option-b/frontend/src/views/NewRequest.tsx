import { useMemo, useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ActionButton, Card, Empty, ErrorBox, Field, Input, Loading, PageHeader, Select, Tabs, Textarea } from "../components/ui";
import { api, ApiError, type Project, type RunSummary } from "../lib/api";

const FIELDS = [
  ["title", "Title", "", true],
  ["problem", "Problem", "what is wrong or missing today", true],
  ["outcome", "Desired outcome", "one line per outcome; each becomes an acceptance criterion", true],
  ["users_and_systems", "Affected users and systems", "", false],
  ["constraints", "Constraints", "", false],
  ["open_questions", "Open questions", "start a line with [blocking] if work must wait for an answer", false],
] as const;
type Key = (typeof FIELDS)[number][0];
type Errors = Partial<Record<Key | "project", string>>;

const BRIEF: DocField[] = [
  ["title", "Product name", "", true],
  ["summary", "Summary", "what the product is for and why it is built now", true],
  ["users", "Users and roles", "one role per line, like HR: manages employees and reads reports", true],
  ["features", "Wanted features", "one feature per line; the Business Analyst splits them into user stories", true],
  ["constraints", "Constraints", "deadline, compliance, devices, integrations; start a line with [blocking] if the SRS must wait for an answer", false],
  ["sources", "Sources", "notes from sales, meetings and the contract that the SRS should trace to", false],
];
const ARCHITECTURE: DocField[] = [
  ["title", "Product name", "as in SRS.md", true],
  ["stack", "Technology and stack", "languages, frameworks, database, devices the team will use or must use", true],
  ["deployment", "Deployment and infrastructure", "where it runs: cloud, on-premise, edge devices, environments", false],
  ["integrations", "Integrations", "systems it talks to, such as HR, payroll, identity provider", false],
  ["notes", "Notes", "security, data protection, performance targets; start a line with [blocking] if the SDS must wait for an answer", false],
];
type DocField = readonly [string, string, string, boolean];
type Kind = "feature" | "srs" | "sds";

function validateDoc(fields: readonly DocField[], values: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [key, label, , required] of fields) {
    const value = (values[key] ?? "").trim();
    if (key === "title" && value.length < 3) errors.title = value ? `${label} needs at least 3 characters` : `${label} is required`;
    else if (required && !value) errors[key] = `${label} is required`;
  }
  return errors;
}

/** A project document request: a brief the Business Analyst turns into SRS.md, or an architecture brief the
 * Architect turns into SDS.md. Both come before any feature run. */
function DocumentForm({ project, fields, bodyKey, submit }: { project: string; fields: readonly DocField[]; bodyKey: "brief" | "architecture"; submit: string }) {
  const [, navigate] = useLocation();
  const idempotencyKey = useMemo(() => crypto.randomUUID(), []);
  const [brief, setBrief] = useState<Record<string, string>>(() => Object.fromEntries(fields.map(([k]) => [k, ""])));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tried, setTried] = useState(false);
  const update = (key: string, value: string) => {
    const next = { ...brief, [key]: value };
    setBrief(next);
    if (tried) setErrors(validateDoc(fields, next));
  };
  return (
    <>
      {fields.map(([key, label, hint, required]) => (
        <Field key={key} label={label} hint={hint || undefined} htmlFor={`brief-${key}`} required={required} error={errors[key]}>
          {key === "title" ? (
            <Input id={`brief-${key}`} value={brief[key]} maxLength={200} aria-invalid={errors[key] ? true : undefined} onChange={(e) => update(key, e.target.value)} />
          ) : (
            <Textarea id={`brief-${key}`} value={brief[key]} aria-invalid={errors[key] ? true : undefined} onChange={(e) => update(key, e.target.value)} />
          )}
        </Field>
      ))}
      <div>
        <ActionButton variant="primary" action={async () => {
          setTried(true);
          const local = validateDoc(fields, brief);
          setErrors(local);
          if (Object.keys(local).length) {
            document.getElementById(`brief-${Object.keys(local)[0]}`)?.focus();
            throw new ApiError(`Fill in ${Object.keys(local).length} required field(s) before submitting.`);
          }
          try {
            const run = await api<RunSummary>("/api/runs", { method: "POST", body: { project, [bodyKey]: brief, idempotency_key: idempotencyKey } });
            navigate(`/runs/${run.id}`);
          } catch (error) {
            const returned = error instanceof ApiError ? error.fields : {};
            if (Object.keys(returned).length) setErrors(returned);
            throw error;
          }
        }}>{submit}</ActionButton>
      </div>
    </>
  );
}

/** The same rules as the controller's Intent contract, checked before sending. */
function validate(intent: Record<Key, string>): Errors {
  const errors: Errors = {};
  if (intent.title.trim().length < 3) errors.title = intent.title.trim() ? "Title needs at least 3 characters" : "Title is required";
  if (intent.title.length > 200) errors.title = "Title is too long (at most 200 characters)";
  if (!intent.problem.trim()) errors.problem = "Problem is required: say what is wrong or missing today";
  if (!intent.outcome.trim()) errors.outcome = "Desired outcome is required: write at least one line; each line becomes an acceptance criterion";
  return errors;
}

function focusFirst(errors: Errors) {
  const first = FIELDS.map(([key]) => key).find((key) => errors[key]);
  if (first) document.getElementById(first)?.focus();
}

export function NewRequestView() {
  const [, navigate] = useLocation();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
  const idempotencyKey = useMemo(() => crypto.randomUUID(), []);
  const [project, setProject] = useState("");
  const [kind, setKind] = useState<Kind>("feature");
  const [free, setFree] = useState("");
  const [intent, setIntent] = useState<Record<Key, string>>({ title: "", problem: "", outcome: "", users_and_systems: "", constraints: "", open_questions: "" });
  const [errors, setErrors] = useState<Errors>({});
  const [tried, setTried] = useState(false);
  if (projects.isLoading) return <Loading />;
  if (projects.error) return <ErrorBox error={projects.error} />;
  const ready = (projects.data ?? []).filter((p) => p.readiness !== "not_ready");
  if (!ready.length) {
    return (<><PageHeader title="New request" /><Empty>No project is ready yet. <Link href="/projects">Add a project</Link> first.</Empty></>);
  }
  const chosen = project || ready[0].key;
  const update = (key: Key, value: string) => {
    const next = { ...intent, [key]: value };
    setIntent(next);
    if (tried) setErrors(validate(next));  // after a first attempt, errors clear as the user types
  };
  const missing = Object.keys(validate(intent)).length;
  return (
    <>
      <PageHeader title="New request" subtitle={{
        srs: "Step 1 of a new project, as Product Owner: the Business Analyst writes SRS.md from this brief.",
        sds: "Step 2 of a new project, as Tech Lead: the Architect writes SDS.md from the merged SRS.md and this brief.",
        feature: "You act as Product Owner. This form is intent.md; the Business Analyst turns it into spec.md.",
      }[kind]} />
      <Tabs<Kind> value={kind} onChange={setKind} tabs={[{ id: "feature", label: "Feature request" }, { id: "srs", label: "Project SRS" }, { id: "sds", label: "Project SDS" }]} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[999_1_560px] flex-col gap-5">
          <Card className="flex flex-col gap-4 p-5">
            <p className="m-0 text-sm text-muted"><span className="text-bad">*</span> required</p>
            <Field label="Project" htmlFor="project" required error={errors.project}>
              <Select id="project" value={chosen} onChange={(e) => setProject(e.target.value)}>
                {ready.map((p) => <option key={p.key} value={p.key}>{p.key} ({p.readiness})</option>)}
              </Select>
            </Field>
            {kind === "srs" ? <DocumentForm key="srs" project={chosen} fields={BRIEF} bodyKey="brief" submit="Submit brief, write SRS.md" />
        : kind === "sds" ? <DocumentForm key="sds" project={chosen} fields={ARCHITECTURE} bodyKey="architecture" submit="Submit, write SDS.md" /> : FIELDS.map(([key, label, hint, required]) => {
              const props = {
                id: key,
                value: intent[key],
                required,
                "aria-invalid": errors[key] ? true : undefined,
                "aria-describedby": errors[key] ? `${key}-error` : undefined,
                onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => update(key, e.target.value),
              };
              return (
                <Field key={key} label={label} hint={hint || undefined} htmlFor={key} required={required} error={errors[key]}>
                  {key === "title" ? <Input {...props} maxLength={200} /> : (
                    <Textarea {...props} placeholder={key === "outcome" ? "Example (replace it):\n- Employees can see their total working hours per week\n- A week without check-ins shows 0 hours" : undefined} />
                  )}
                </Field>
              );
            })}
            {kind !== "feature" ? null : <div className="flex flex-wrap items-center gap-3">
              <ActionButton variant="primary" action={async () => {
                setTried(true);
                const local = validate(intent);
                setErrors(local);
                if (Object.keys(local).length) {
                  focusFirst(local);
                  throw new ApiError(`Fill in ${Object.keys(local).length} required field(s) before submitting.`);
                }
                try {
                  const run = await api<RunSummary>("/api/runs", { method: "POST", body: { project: chosen, intent, idempotency_key: idempotencyKey } });
                  navigate(`/runs/${run.id}`);
                } catch (error) {
                  const fields = error instanceof ApiError ? error.fields : {};
                  if (Object.keys(fields).length) { setErrors(fields as Errors); focusFirst(fields as Errors); }
                  throw error;
                }
              }}>Submit intent.md</ActionButton>
              {tried && missing ? <span className="text-sm text-bad">{missing} required field(s) missing</span> : null}
            </div>}
          </Card>
        </div>
        {kind !== "feature" ? null : <Card className="flex min-w-0 flex-[1_1_300px] flex-col gap-3 p-5">
          <h2 className="m-0 flex items-center gap-2 text-base font-semibold"><Sparkles className="size-4 text-accent" aria-hidden />Draft with AI</h2>
          <p className="m-0 text-sm text-muted">Describe what you want in your own words. The Business Analyst fills the form; correct it before you submit.</p>
          <Field label="Your words" htmlFor="free">
            <Textarea id="free" className="min-h-40" value={free} onChange={(e) => setFree(e.target.value)} placeholder="Example: employees should see how many hours they worked this week, and managers want the late days per person" />
          </Field>
          <div>
            <ActionButton action={async () => {
              if (!free.trim()) throw new ApiError("Write a few words first; the Business Analyst drafts from them.");
              const draft = await api<Record<Key, string>>("/api/intents/draft", { method: "POST", body: { title: intent.title.trim().length >= 3 ? intent.title : "Untitled request", text: free } });
              const next = Object.fromEntries(FIELDS.map(([k]) => [k, draft[k] ?? ""])) as Record<Key, string>;
              setIntent(next);
              if (tried) setErrors(validate(next));
              toast.success("Draft filled in. Submitting accepts it as the Product Owner.");
            }}>Draft intent.md</ActionButton>
          </div>
        </Card>}
      </div>
    </>
  );
}
