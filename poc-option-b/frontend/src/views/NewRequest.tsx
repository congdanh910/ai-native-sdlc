import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ActionButton, Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, PageHeader, Select, Tabs, Textarea } from "../components/ui";
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
type Errors = Partial<Record<Key | "project" | "story", string>>;

interface Stories {
  has_srs: boolean;
  features: { section: string; name: string; prefix: string;
    stories: { id: string; title: string; actors: string; statement: string; run: { id: string; status: string } | null }[] }[];
}

/** "**As a** `HR`, **I want to** ..." as plain text for the Problem field. */
function plain(statement: string): string {
  return statement.replace(/\*\*/g, "").replace(/`/g, "");
}

const BRIEF: DocField[] = [
  ["title", "Product name", "", true],
  ["summary", "Summary", "what the product is for and why it is built now", true],
  ["users", "Users and roles", "one role per line, like HR: manages employees and reads reports", true],
  ["features", "Wanted features", "one feature per line; the Business Analyst splits them into user stories", true],
  ["constraints", "Constraints", "deadline, compliance, devices, integrations; start a line with [blocking] if the SRS must wait for an answer", false],
  ["sources", "Sources", "notes from sales, meetings and the contract that the SRS should trace to", false],
];
const DISCOVERY: DocField[] = [
  ["title", "Name", "the product or project, as the team calls it", true],
  ["notes", "Notes", "anything the sources do not say: who asked, deadline, what is already decided", false],
];
const ARCHITECTURE: DocField[] = [
  ["title", "Product name", "as in SRS.md", true],
  ["stack", "Technology and stack", "languages, frameworks, database, devices the team will use or must use", true],
  ["deployment", "Deployment and infrastructure", "where it runs: cloud, on-premise, edge devices, environments", false],
  ["integrations", "Integrations", "systems it talks to, such as HR, payroll, identity provider", false],
  ["notes", "Notes", "security, data protection, performance targets; start a line with [blocking] if the SDS must wait for an answer", false],
];
type DocField = readonly [string, string, string, boolean];
const FOUNDATION: DocField[] = [
  ["title", "Name", "the product, as in SDS.md", true],
  ["notes", "Notes", "what the skeleton must include or leave out, such as \"no CI yet\" or \"sign-in with the company SSO later\"", false],
];
// The customer's environment: a stated platform makes the Architect design on it; left open, it compares platforms.
const PLATFORMS: [string, string][] = [["", "Not decided yet: compare the platforms"], ["aws", "AWS"], ["azure", "Microsoft Azure"],
  ["gcp", "Google Cloud"], ["on_premise", "On-premise"], ["hybrid", "Hybrid"]];
const BRAND_FIELDS: readonly [string, string, string][] = [
  ["colors", "Brand colors", "hex, primary first, such as #1F5FAD #E8590C"],
  ["fonts", "Fonts", "comma separated, such as Inter, Roboto"],
  ["figma_url", "Figma file", "a link; the concepts then follow it"],
  ["tone", "Tone", "formal, friendly ..."],
  ["language", "Interface language", "such as Vietnamese"],
];

const DEPLOYMENT_CONTEXT: [string, string, string][] = [
  ["existing_infrastructure", "Existing infrastructure", "cloud contracts, data centre, servers, network the customer already has"],
  ["data_residency", "Data residency", "where personal and biometric data must stay, such as in Vietnam or on the customer's premises"],
  ["operations_team", "Operations team", "who will run it and what they know (Linux, Kubernetes, Windows Server ...)"],
  ["budget", "Budget", "for hosting and licences, if known"],
  ["scale", "Scale", "sites, users, cameras or devices, expected growth"],
  ["availability", "Availability", "uptime, maintenance windows, how fast it must recover"],
];
type Kind = "discovery" | "feature" | "srs" | "sds" | "foundation";

function validateDoc(fields: readonly DocField[], values: Record<string, string>, onlyTitle = false): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [key, label, , required] of fields) {
    const value = (values[key] ?? "").trim();
    if (key === "title" && value.length < 3) errors.title = value ? `${label} needs at least 3 characters` : `${label} is required`;
    else if (required && !onlyTitle && !value) errors[key] = `${label} is required`;
  }
  return errors;
}

/** A project document request: a brief the Business Analyst turns into SRS.md, or an architecture brief the
 * Architect turns into SDS.md. Both come before any feature run. */
function DocumentForm({ project, fields, bodyKey, submit, allowBaseline, allowProposal, askDeployment }: { project: string; fields: readonly DocField[]; bodyKey: "brief" | "architecture" | "discovery" | "foundation"; submit: string; allowBaseline?: boolean; allowProposal?: boolean; askDeployment?: boolean }) {
  const [deployment, setDeployment] = useState<Record<string, string>>({ platform: "" });
  const [brand, setBrand] = useState<Record<string, string>>({ colors: "", fonts: "", figma_url: "", tone: "", language: "" });
  const [devices, setDevices] = useState<string[]>(["desktop", "mobile"]);
  const [accessibility, setAccessibility] = useState("wcag-aa");
  const [logo, setLogo] = useState<{ name: string; data: string } | null>(null);
  const [fromBaseline, setFromBaseline] = useState(false);
  const [propose, setPropose] = useState(!!allowProposal);  // the Architect proposes the stack in the Options step
  const [, navigate] = useLocation();
  const idempotencyKey = useMemo(() => crypto.randomUUID(), []);
  const [brief, setBrief] = useState<Record<string, string>>(() => Object.fromEntries(fields.map(([k]) => [k, ""])));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tried, setTried] = useState(false);
  const update = (key: string, value: string) => {
    const next = { ...brief, [key]: value };
    setBrief(next);
    if (tried) setErrors(validateDoc(fields, next, fromBaseline || propose));
  };
  const shown = fromBaseline ? fields.filter(([key]) => key === "title" || key === "summary" || key === "constraints") : fields;
  return (
    <>
      {allowProposal ? (
        <label className="flex items-start gap-2 rounded-lg border border-line p-3 text-sm">
          <input type="checkbox" className="mt-1" checked={propose} onChange={(e) => { setPropose(e.target.checked); setErrors({}); }} />
          <span><b>Let the Architect propose the stack</b>, deployment and integrations from the SRS, the requirement baseline and the repository. It compares options with a full stack each; the Solution Architect picks one, adjusts it and confirms before the SDS is written. What you fill in below is a constraint the options must follow.</span>
        </label>
      ) : null}
      {allowBaseline ? (
        <label className="flex items-start gap-2 rounded-lg border border-line p-3 text-sm">
          <input type="checkbox" className="mt-1" checked={fromBaseline} onChange={(e) => { setFromBaseline(e.target.checked); setErrors({}); }} />
          <span><b>Build on the discovery baseline</b> (requirements/baseline.json merged on the default branch). The Business Analyst writes the SRS from the baseline items and decisions, and an appendix traces every item to an FR, a story or a section. Summary and constraints become optional notes.</span>
        </label>
      ) : null}
      {shown.map(([key, label, hint, required]) => (
        <Field key={key} label={fromBaseline && key === "summary" ? "Notes" : label} hint={(fromBaseline || propose) && key !== "title" ? `optional${propose ? ": a constraint the options must follow" : ""}` : hint || undefined} htmlFor={`brief-${key}`} required={required && (!(fromBaseline || propose) || key === "title")} error={errors[key]}>
          {key === "title" ? (
            <Input id={`brief-${key}`} value={brief[key]} maxLength={200} aria-invalid={errors[key] ? true : undefined} onChange={(e) => update(key, e.target.value)} />
          ) : (
            <Textarea id={`brief-${key}`} value={brief[key]} aria-invalid={errors[key] ? true : undefined} onChange={(e) => update(key, e.target.value)} />
          )}
        </Field>
      ))}
      {askDeployment ? (
        <fieldset className="flex flex-col gap-3 rounded-lg border border-line p-3">
          <legend className="px-1 text-sm font-semibold">Deployment context</legend>
          <p className="m-0 text-sm text-muted">Where the customer will run it. A stated platform makes the Architect design the deployment on it; left open, the Architect compares platforms and the Solution Architect chooses with a reason. Fill in what you know.</p>
          <Field label="Hosting platform" htmlFor="deploy-platform">
            <Select id="deploy-platform" value={deployment.platform} onChange={(e) => setDeployment({ ...deployment, platform: e.target.value })}>
              {PLATFORMS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </Field>
          {DEPLOYMENT_CONTEXT.map(([key, label, hint]) => (
            <Field key={key} label={label} hint={`optional: ${hint}`} htmlFor={`deploy-${key}`}>
              <Input id={`deploy-${key}`} value={deployment[key] ?? ""} onChange={(e) => setDeployment({ ...deployment, [key]: e.target.value })} />
            </Field>
          ))}
        </fieldset>
      ) : null}
      {askDeployment ? (
        <fieldset className="flex flex-col gap-3 rounded-lg border border-line p-3">
          <legend className="px-1 text-sm font-semibold">Brand and UX</legend>
          <p className="m-0 text-sm text-muted">What the customer gives for the user interface; all optional. The UX Designer proposes UI concepts in this brand (or follows the Figma file); with nothing given, the platform's default concepts are offered.</p>
          <Field label="Logo" hint="optional: PNG, SVG, JPG or WebP" htmlFor="brand-logo">
            <input id="brand-logo" type="file" accept=".png,.svg,.jpg,.jpeg,.webp" onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) { setLogo(null); return; }
              const reader = new FileReader();
              reader.onload = () => setLogo({ name: file.name, data: String(reader.result).split(",")[1] ?? "" });
              reader.readAsDataURL(file);
            }} />
          </Field>
          {BRAND_FIELDS.map(([key, label, hint]) => (
            <Field key={key} label={label} hint={`optional: ${hint}`} htmlFor={`brand-${key}`}>
              <Input id={`brand-${key}`} value={brand[key] ?? ""} onChange={(e) => setBrand({ ...brand, [key]: e.target.value })} />
            </Field>
          ))}
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="font-semibold">Devices</span>
            {["desktop", "tablet", "mobile", "kiosk"].map((d) => (
              <label key={d} className="flex items-center gap-1">
                <input type="checkbox" checked={devices.includes(d)} onChange={(e) => setDevices(e.target.checked ? [...devices, d] : devices.filter((x) => x !== d))} /> {d}
              </label>
            ))}
          </div>
          <Field label="Accessibility" htmlFor="brand-a11y">
            <Select id="brand-a11y" value={accessibility} onChange={(e) => setAccessibility(e.target.value)}>
              <option value="wcag-aa">WCAG 2.2 AA (text contrast 4.5:1)</option>
              <option value="wcag-aaa">WCAG 2.2 AAA (7:1)</option>
              <option value="none">No requirement</option>
            </Select>
          </Field>
          <BrandPreview project={project} product={brief.title ?? ""} colors={brand.colors} fonts={brand.fonts} figma={brand.figma_url}
            logo={logo} devices={devices} accessibility={accessibility} />
        </fieldset>
      ) : null}
      <div>
        <ActionButton variant="primary" action={async () => {
          setTried(true);
          const local = validateDoc(fields, brief, fromBaseline || propose);
          setErrors(local);
          if (Object.keys(local).length) {
            document.getElementById(`brief-${Object.keys(local)[0]}`)?.focus();
            throw new ApiError(`Fill in ${Object.keys(local).length} required field(s) before submitting.`);
          }
          try {
            const body = fromBaseline ? { ...brief, from_baseline: true }
              : allowProposal ? { ...brief, propose_stack: propose, ...(askDeployment ? {
                deployment_context: deployment,
                brand: {
                  colors: brand.colors.split(/[\s,]+/).filter(Boolean), fonts: brand.fonts.split(",").map((f) => f.trim()).filter(Boolean),
                  figma_url: brand.figma_url.trim(), tone: brand.tone.trim(), language: brand.language.trim(),
                  devices: devices.length ? devices : ["desktop"], accessibility,
                },
                ...(logo ? { logo } : {}),
              } : {}) } : brief;
            const run = await api<RunSummary>("/api/runs", { method: "POST", body: { project, [bodyKey]: body, idempotency_key: idempotencyKey } });
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
  const [story, setStory] = useState("");
  const firstReady = (projects.data ?? []).find((p) => p.readiness !== "not_ready")?.key ?? "";
  const stories = useQuery({
    queryKey: ["stories", project || firstReady],
    queryFn: () => api<Stories>(`/api/projects/${encodeURIComponent(project || firstReady)}/stories`),
    enabled: !!(project || firstReady),
  });
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
    if (tried) setErrors(check(next));  // after a first attempt, errors clear as the user types
  };
  const features = stories.data?.has_srs ? stories.data.features.filter((f) => f.stories.length) : [];
  const needsStory = features.length > 0;
  const check = (values: Record<Key, string>): Errors => ({
    ...validate(values),
    ...(needsStory && !story ? { story: "Pick the user story from SRS.md that this run delivers" } : {}),
  });
  const chooseStory = (id: string) => {
    setStory(id);
    const picked = features.flatMap((f) => f.stories).find((s) => s.id === id);
    if (picked) {
      const next = { ...intent, title: intent.title.trim() ? intent.title : picked.title,
        problem: intent.problem.trim() ? intent.problem : plain(picked.statement) };
      setIntent(next);
    }
    if (tried) setErrors({ ...errors, story: id ? undefined : errors.story });
  };
  const missing = Object.keys(check(intent)).length;
  return (
    <>
      <PageHeader title="New request" subtitle={{
        discovery: "Step 0 of a new project, as Product Owner: collect the raw requirements (decks, documents, sheets, minutes); the Business Analyst extracts a requirement inventory and the questions to clarify.",
        foundation: "Step 3 of a new project, as Tech Lead: after SDS.md is merged, the onboarding files follow the design and the team builds the walking skeleton before the first user story.",
        srs: "Step 1 of a new project, as Product Owner: the Business Analyst writes SRS.md from this brief.",
        sds: "Step 2 of a new project, as Tech Lead: the Architect writes SDS.md from the merged SRS.md and this brief.",
        feature: "You act as Product Owner. This form is intent.md; the Business Analyst turns it into spec.md.",
      }[kind]} />
      <Tabs<Kind> value={kind} onChange={setKind} tabs={[{ id: "feature", label: "Feature request" }, { id: "discovery", label: "Discovery" }, { id: "srs", label: "Project SRS" }, { id: "sds", label: "Project SDS" }, { id: "foundation", label: "Foundation" }]} />
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[999_1_560px] flex-col gap-5">
          <Card className="flex flex-col gap-4 p-5">
            <p className="m-0 text-sm text-muted"><span className="text-bad">*</span> required</p>
            <Field label="Project" htmlFor="project" required error={errors.project}>
              <Select id="project" value={chosen} onChange={(e) => setProject(e.target.value)}>
                {ready.map((p) => <option key={p.key} value={p.key}>{p.key} ({p.readiness})</option>)}
              </Select>
            </Field>
            {kind === "feature" && needsStory ? (
              <Field label="User story" hint="from SRS.md section 7; the run writes its sections in specs/ for this story's feature" htmlFor="story" required error={errors.story}>
                <Select id="story" value={story} onChange={(e) => chooseStory(e.target.value)} aria-invalid={errors.story ? true : undefined}>
                  <option value="">Choose a user story…</option>
                  {features.map((f) => (
                    <optgroup key={f.prefix} label={`${f.section} ${f.name} (${f.prefix})`}>
                      {f.stories.map((s) => (
                        <option key={s.id} value={s.id}>{s.id}: {s.title} ({s.actors}){s.run ? ` · ${s.run.status.toLowerCase().replace(/_/g, " ")}` : ""}</option>
                      ))}
                    </optgroup>
                  ))}
                </Select>
              </Field>
            ) : null}
            {kind === "discovery" ? <DocumentForm key="discovery" project={chosen} fields={DISCOVERY} bodyKey="discovery" submit="Start discovery, then add sources" />
              : kind === "srs" ? <DocumentForm key="srs" project={chosen} fields={BRIEF} bodyKey="brief" submit="Write SRS.md" allowBaseline />
        : kind === "sds" ? <DocumentForm key="sds" project={chosen} fields={ARCHITECTURE} bodyKey="architecture" submit="Submit, write SDS.md" allowProposal askDeployment />
        : kind === "foundation" ? (
          <>
            <p className="m-0 text-sm text-muted">After SDS.md is merged and before any user story: the Architect rewrites the onboarding files (AGENTS.md, REVIEW.md, .sdlc/project.yaml, the validation environment) from the SDS for the Tech Lead to approve, the platform builds and tries that environment, then the team builds the walking skeleton every story stands on (structure, tooling, migrations, health check, sign-in and roles) through spec, plan, test cases, code, review and a pull request.</p>
            <DocumentForm key="foundation" project={chosen} fields={FOUNDATION} bodyKey="foundation" submit="Start the foundation run" />
          </>
        ) : FIELDS.map(([key, label, hint, required]) => {
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
                  {key === "outcome" && story ? (
                    <OutcomeSuggestions project={chosen} story={story} onUse={(lines) => update("outcome", lines)} current={intent.outcome} />
                  ) : null}
                </Field>
              );
            })}
            {kind !== "feature" ? null : <div className="flex flex-wrap items-center gap-3">
              <ActionButton variant="primary" action={async () => {
                setTried(true);
                const local = check(intent);
                setErrors(local);
                if (Object.keys(local).length) {
                  focusFirst(local);
                  throw new ApiError(`Fill in ${Object.keys(local).length} required field(s) before submitting.`);
                }
                try {
                  const run = await api<RunSummary>("/api/runs", { method: "POST", body: { project: chosen, intent: { ...intent, story }, idempotency_key: idempotencyKey } });
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
              if (tried) setErrors(check(next));
              toast.success("Draft filled in. Submitting accepts it as the Product Owner.");
            }}>Draft intent.md</ActionButton>
          </div>
        </Card>}
      </div>
    </>
  );
}


type Suggestion = { text: string; source: string };

/** Desired outcomes drafted from the SRS and SDS of the chosen story (each with its source), or written by the AI from
 * the same sources; the person picks, edits and submits. */
function OutcomeSuggestions({ project, story, current, onUse }: { project: string; story: string; current: string; onUse: (lines: string) => void }) {
  const drafted = useQuery({
    queryKey: ["outcomes", project, story],
    queryFn: () => api<{ suggestions: Suggestion[] }>(`/api/projects/${encodeURIComponent(project)}/stories/${encodeURIComponent(story)}/outcomes`),
  });
  const [ai, setAi] = useState<{ suggestions: Suggestion[]; notes: string } | null>(null);
  const [picked, setPicked] = useState<Set<number> | null>(null);
  const lines = ai?.suggestions ?? drafted.data?.suggestions ?? [];
  const chosen = picked ?? new Set(lines.map((_, i) => i));
  if (drafted.isLoading) return <p className="m-0 mt-2 text-xs text-muted">Reading the SRS and SDS of {story}…</p>;
  return (
    <div className="mt-2 flex flex-col gap-2 rounded-lg border border-line bg-side p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <b>Suggested outcomes</b>
        <span className="text-xs text-muted">{ai ? "written by the AI from the SRS and SDS" : "from the SRS requirements and rules, the SDS APIs and errors of the story"}</span>
      </div>
      {lines.length ? (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {lines.map((line, i) => (
            <li key={`${i}-${line.text}`}>
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1" checked={chosen.has(i)} onChange={(e) => {
                  const next = new Set(chosen);
                  if (e.target.checked) next.add(i); else next.delete(i);
                  setPicked(next);
                }} />
                <span>{line.text} {line.source ? <span className="text-xs text-muted">({line.source})</span> : null}</span>
              </label>
            </li>
          ))}
        </ul>
      ) : <p className="m-0 text-xs text-muted">The SRS and SDS say little about this story; ask the AI, or write the outcomes yourself.</p>}
      {ai?.notes ? <p className="m-0 text-xs text-muted">{ai.notes}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={!chosen.size} onClick={() => {
          const text = lines.filter((_, i) => chosen.has(i)).map((l) => `- ${l.text}`).join("\n");
          onUse(current.trim() ? `${current.trim()}\n${text}` : text);
        }}>{current.trim() ? "Add the ticked lines" : "Use the ticked lines"}</Button>
        <ActionButton success="The AI wrote the outcomes; tick the ones to use" action={async () => {
          const answer = await api<{ suggestions: Suggestion[]; notes: string }>(`/api/projects/${encodeURIComponent(project)}/stories/${encodeURIComponent(story)}/outcomes/ai`, { method: "POST" });
          setAi(answer);
          setPicked(null);
        }}>Suggest with AI (about a minute)</ActionButton>
      </div>
    </div>
  );
}


interface PresetInfo { id: string; name: string; best_for: string; layout: string; density: string }
interface PreviewAnswer { html: string; screens: string[]; checks: { pair: string; ratio: number; ok: boolean; need: number }[]; ignored_colors: string[] }
const DEVICE_WIDTH: Record<string, number> = { desktop: 1280, tablet: 820, mobile: 390, kiosk: 1080 };

/** What the UI could look like before the SDS run: the platform's default concepts drawn by the controller with the
 * brand typed so far (colors, fonts, logo), on sample screens with the project's features; or the customer's Figma
 * file embedded. The UX Designer later proposes the real concepts from these, with the SRS screens. */
function BrandPreview({ project, product, colors, fonts, figma, logo, devices, accessibility }: {
  project: string; product: string; colors: string; fonts: string; figma: string; logo: { name: string; data: string } | null;
  devices: string[]; accessibility: string;
}) {
  const presets = useQuery({ queryKey: ["ui-presets"], queryFn: () => api<PresetInfo[]>("/api/ui/presets"), staleTime: 300_000 });
  const [preset, setPreset] = useState("");
  const [device, setDevice] = useState("desktop");
  const [screen, setScreen] = useState("overview");
  const chosen = preset || presets.data?.[0]?.id || "";
  const shownDevice = devices.includes(device) ? device : devices[0] ?? "desktop";
  const logoUri = logo ? `data:${logo.name.toLowerCase().endsWith(".svg") ? "image/svg+xml" : `image/${logo.name.split(".").pop()?.toLowerCase().replace("jpg", "jpeg")}`};base64,${logo.data}` : "";
  const body = { preset: chosen, colors: colors.split(/[\s,]+/).filter(Boolean), fonts: fonts.split(",").map((f) => f.trim()).filter(Boolean),
    product, project, logo: logoUri, device: shownDevice, screen, accessibility };
  const preview = useQuery({
    queryKey: ["ui-preview", body], enabled: !!chosen, placeholderData: (previous) => previous,
    queryFn: () => api<PreviewAnswer>("/api/ui/preview", { method: "POST", body }),
  });
  const figmaUrl = /^https:\/\/(www\.)?figma\.com\//.test(figma.trim()) ? figma.trim() : "";
  const width = DEVICE_WIDTH[shownDevice] ?? 1280;
  const frame = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(900);
  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setRoom(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const scale = Math.min(1, room / width);
  const height = shownDevice === "mobile" ? 760 : 720;
  return (
    <div ref={frame} className="flex flex-col gap-3 rounded-lg border border-line p-3">
      <div>
        <b className="text-sm">Preview</b>{" "}
        <span className="text-xs text-muted">{figmaUrl ? "the customer's Figma file; the concepts follow it" : "the platform's default concepts with the brand above, on sample screens; the UX Designer then proposes concepts with the real screens of the SRS"}</span>
      </div>
      {figmaUrl ? (
        <>
          <iframe title="Figma file" className="h-[520px] w-full rounded border border-line" allowFullScreen
            src={`https://www.figma.com/embed?embed_host=sdlc-control-room&url=${encodeURIComponent(figmaUrl)}`} />
          <p className="m-0 text-xs text-muted">Empty or asking to sign in? Share the file as "anyone with the link can view" in Figma.</p>
        </>
      ) : null}
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Default concepts">
        {(presets.data ?? []).map((p) => (
          <button key={p.id} type="button" role="tab" aria-selected={chosen === p.id} onClick={() => setPreset(p.id)}
            className={`rounded-lg border px-3 py-1.5 text-left text-xs ${chosen === p.id ? "border-accent bg-accent-soft text-accent-ink" : "border-line hover:bg-sunken"}`}>
            <b className="block text-sm">{p.name}</b>{p.best_for}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Select aria-label="Device" className="w-auto" value={shownDevice} onChange={(e) => setDevice(e.target.value)}>
          {(devices.length ? devices : ["desktop"]).map((d) => <option key={d} value={d}>{d}</option>)}
        </Select>
        {(preview.data?.screens ?? ["overview", "form", "sign-in", "empty"]).map((name) => (
          <Button key={name} variant={screen === name ? "primary" : undefined} onClick={() => setScreen(name)}>{name}</Button>
        ))}
      </div>
      {preview.error ? <ErrorBox error={preview.error} /> : null}
      {preview.data ? (
        <>
          <div className="overflow-hidden rounded-lg border border-line bg-sunken shadow-sm" style={{ height: height * scale, width: width * scale }}>
            <iframe title="Concept preview" sandbox="" srcDoc={preview.data.html}
              style={{ width, height, transform: `scale(${scale})`, transformOrigin: "0 0", border: 0, display: "block" }} />
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {preview.data.checks.map((c) => <Badge key={c.pair} tone={c.ok ? "good" : "bad"}>{c.pair} {c.ratio}:1{c.ok ? "" : ` (needs ${c.need}:1)`}</Badge>)}
          </div>
          {preview.data.ignored_colors.length ? <p className="m-0 text-xs text-bad">Not a #RRGGBB color, ignored: {preview.data.ignored_colors.join(", ")}</p> : null}
        </>
      ) : <p className="m-0 text-xs text-muted">Drawing the preview…</p>}
    </div>
  );
}
