import { useMemo, useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ActionButton, Card, Empty, ErrorBox, Field, Input, Loading, PageHeader, Select, Textarea } from "../components/ui";
import { api, type Project, type RunSummary } from "../lib/api";

const FIELDS = [
  ["title", "Title", ""],
  ["problem", "Problem", "what is wrong or missing today"],
  ["outcome", "Desired outcome", "one line per outcome; each becomes an acceptance criterion"],
  ["users_and_systems", "Affected users and systems", ""],
  ["constraints", "Constraints", ""],
  ["open_questions", "Open questions", "start a line with [blocking] if work must wait for an answer"],
] as const;
type Key = (typeof FIELDS)[number][0];

export function NewRequestView() {
  const [, navigate] = useLocation();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
  const idempotencyKey = useMemo(() => crypto.randomUUID(), []);
  const [project, setProject] = useState("");
  const [free, setFree] = useState("");
  const [intent, setIntent] = useState<Record<Key, string>>({ title: "", problem: "", outcome: "", users_and_systems: "", constraints: "", open_questions: "" });
  if (projects.isLoading) return <Loading />;
  if (projects.error) return <ErrorBox error={projects.error} />;
  const ready = (projects.data ?? []).filter((p) => p.readiness !== "not_ready");
  if (!ready.length) {
    return (<><PageHeader title="New request" /><Empty>No project is ready yet. <Link href="/projects">Add a project</Link> first.</Empty></>);
  }
  const chosen = project || ready[0].key;
  return (
    <>
      <PageHeader title="New request" subtitle="You act as Product Owner. This form is intent.md; the Business Analyst turns it into spec.md." />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[999_1_560px] flex-col gap-5">
          <Card className="flex flex-col gap-4 p-5">
            <Field label="Project" htmlFor="project">
              <Select id="project" value={chosen} onChange={(e) => setProject(e.target.value)}>
                {ready.map((p) => <option key={p.key} value={p.key}>{p.key} ({p.readiness})</option>)}
              </Select>
            </Field>
            {FIELDS.map(([key, label, hint]) => (
              <Field key={key} label={label} hint={hint || undefined} htmlFor={key}>
                {key === "title" ? (
                  <Input id={key} value={intent[key]} onChange={(e) => setIntent({ ...intent, [key]: e.target.value })} />
                ) : (
                  <Textarea id={key} value={intent[key]} onChange={(e) => setIntent({ ...intent, [key]: e.target.value })} />
                )}
              </Field>
            ))}
            <div>
              <ActionButton variant="primary" action={async () => {
                const run = await api<RunSummary>("/api/runs", { method: "POST", body: { project: chosen, intent, idempotency_key: idempotencyKey } });
                navigate(`/runs/${run.id}`);
              }}>Submit intent.md</ActionButton>
            </div>
          </Card>
        </div>
        <Card className="flex min-w-0 flex-[1_1_300px] flex-col gap-3 p-5">
          <h2 className="m-0 flex items-center gap-2 text-base font-semibold"><Sparkles className="size-4 text-accent" aria-hidden />Draft with AI</h2>
          <p className="m-0 text-sm text-muted">Describe what you want in your own words. The Business Analyst fills the form; correct it before you submit.</p>
          <Field label="Your words" htmlFor="free">
            <Textarea id="free" className="min-h-40" value={free} onChange={(e) => setFree(e.target.value)} placeholder="e.g. let store keepers search items by part of the SKU or name" />
          </Field>
          <div>
            <ActionButton action={async () => {
              const draft = await api<Record<Key, string>>("/api/intents/draft", { method: "POST", body: { title: intent.title || "Untitled request", text: free } });
              setIntent(Object.fromEntries(FIELDS.map(([k]) => [k, draft[k] ?? ""])) as Record<Key, string>);
              toast.success("Draft filled in. Submitting accepts it as the Product Owner.");
            }}>Draft intent.md</ActionButton>
          </div>
        </Card>
      </div>
    </>
  );
}
