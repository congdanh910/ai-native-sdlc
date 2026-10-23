import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Brain, Hammer, Lightbulb, Plus, X } from "lucide-react";
import { ActionButton, Badge, Button, Card, ErrorBox, Loading, Mono, PageHeader, Select } from "../components/ui";
import { api } from "../lib/api";
import { cn, role as roleLabel } from "../lib/utils";

type Choice = { model: string; effort: string };
type Models = Record<string, Record<string, Choice>>;
type Kind = "thinking" | "doing";
interface Providers {
  thinking: Record<string, string[]>; doing: Record<string, string[]>; fallback: boolean; models: Models;
  model_suggestions: Record<string, string[]>; efforts: string[];
  codex_models: { slug: string; name: string; efforts: string[]; default_effort: string }[];
  recommendations: Record<string, { codex?: { model: string; effort?: string }; claude?: { model: string }; why?: string }>;
  thinking_roles: string[]; doing_roles: string[]; thinking_engines: string[]; doing_engines: string[];
  env: Record<string, string>;
  status: { provider: string; limited_until: string | null; reason: string }[];
}
type Draft = Pick<Providers, "thinking" | "doing" | "fallback" | "models">;

const PURPOSE: Record<string, string> = {
  business_analyst: "Specs, SRS, discovery",
  tech_lead: "Plans, SDS, architecture, onboarding, UI concepts",
  qa_analyst: "Test case design",
  code_reviewer: "Reviews, briefs, settles disputes",
  qa_engineer: "Writes test code and acceptance tests",
  developer: "Writes the code",
};
const providerOf = (engine: string) => engine.replace("-readonly", "");
const PROVIDER_NAME: Record<string, string> = { codex: "Codex", claude: "Claude" };
const pick = (p: Providers): Draft => ({ thinking: p.thinking, doing: p.doing, fallback: p.fallback, models: p.models });

/** Platform Admin: each role's providers in order of preference, with the model each one uses. A provider out of its
 * subscription window is marked limited and the role moves to its next provider in the same job. */
export function ProvidersView() {
  const client = useQueryClient();
  const data = useQuery({ queryKey: ["providers"], queryFn: () => api<Providers>("/api/admin/providers"), refetchInterval: 20000 });
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => { if (data.data && !draft) setDraft(pick(data.data)); }, [data.data, draft]);
  if (data.isLoading || !draft) return <Loading />;
  if (data.error) return <ErrorBox error={data.error} />;
  const d = data.data!;
  const dirty = JSON.stringify(draft) !== JSON.stringify(pick(d));
  const store = (fresh: Providers) => { client.setQueryData(["providers"], fresh); setDraft(pick(fresh)); };
  const limited = Object.fromEntries(d.status.map((s) => [s.provider, s.limited_until]));

  const suggested = (role: string, models: Models): Models => {
    const rec = d.recommendations[role];
    if (!rec) return models;
    return { ...models, [role]: {
      ...(models[role] ?? {}),
      ...(rec.codex ? { codex: { model: rec.codex.model, effort: rec.codex.effort ?? "" } } : {}),
      ...(rec.claude ? { claude: { model: rec.claude.model, effort: "" } } : {}),
    } };
  };
  const chainOf = (kind: Kind, role: string): string[] => {
    const listed = draft[kind][role] ?? [];
    return listed.length ? listed : [kind === "thinking" ? d.env[role] ?? "codex-readonly" : "codex"];
  };
  const setChain = (kind: Kind, role: string, list: string[]) => setDraft({ ...draft, [kind]: { ...draft[kind], [role]: list } });
  const choice = (role: string, provider: string): Choice => draft.models[role]?.[provider] ?? { model: "", effort: "" };
  const setChoice = (role: string, provider: string, change: Partial<Choice>) =>
    setDraft({ ...draft, models: { ...draft.models, [role]: { ...(draft.models[role] ?? {}), [provider]: { ...choice(role, provider), ...change } } } });

  // render functions, not components: a component defined here would remount on every keystroke
  const modelControls = (role: string, provider: string) => {
    const current = choice(role, provider);
    if (provider === "claude") {
      return (
        <input aria-label={`${role} model on Claude`} list="models-claude" placeholder="default model"
          className="h-9 w-44 rounded-lg border border-line bg-panel px-2.5 font-mono text-[12.5px]"
          value={current.model} onChange={(e) => setChoice(role, provider, { model: e.target.value })} />
      );
    }
    const model = d.codex_models.find((m) => m.slug === current.model);
    return (
      <div className="flex flex-wrap gap-1.5">
        <Select aria-label={`${role} model on Codex`} className="h-9 w-40 text-sm" value={current.model}
          onChange={(e) => {
            const next = d.codex_models.find((m) => m.slug === e.target.value);
            setChoice(role, provider, { model: e.target.value, effort: next && current.effort && !next.efforts.includes(current.effort) ? "" : current.effort });
          }}>
          <option value="">default model</option>
          {d.codex_models.map((m) => <option key={m.slug} value={m.slug}>{m.name}</option>)}
          {current.model && !model ? <option value={current.model}>{current.model}</option> : null}
        </Select>
        <Select aria-label={`${role} reasoning effort on Codex`} className="h-9 w-40 text-sm" value={current.effort}
          onChange={(e) => setChoice(role, provider, { effort: e.target.value })}>
          <option value="">effort: default{model?.default_effort ? ` (${model.default_effort})` : ""}</option>
          {(model?.efforts ?? d.efforts).map((e) => <option key={e} value={e}>effort: {e}</option>)}
        </Select>
      </div>
    );
  };

  const roleCard = (kind: Kind, role: string) => {
    const listed = (draft[kind][role] ?? []).length > 0;
    const chain = chainOf(kind, role);
    const every = kind === "thinking" ? d.thinking_engines : d.doing_engines;
    const rest = every.filter((e) => !chain.includes(e));
    const rec = d.recommendations[role];
    const move = (i: number, by: number) => { const n = [...chain]; [n[i], n[i + by]] = [n[i + by], n[i]]; setChain(kind, role, n); };
    return (
      <Card key={role} className="flex flex-col gap-3 p-4">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="m-0 text-[15px] font-semibold">{roleLabel(role)}</h3>
            <p className="m-0 text-xs text-muted">{PURPOSE[role]}</p>
          </div>
          {!listed ? <Badge tone="neutral">from {kind === "thinking" ? ".env" : "project.yaml"}</Badge> : null}
        </div>
        <ol className="m-0 flex list-none flex-col gap-2 p-0">
          {chain.map((engine, i) => {
            const provider = providerOf(engine);
            return (
              <li key={engine} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-sunken/40 p-2">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-panel text-xs font-semibold text-muted" title={i === 0 ? "first choice" : "fallback"}>{i + 1}</span>
                <span className="w-14 shrink-0 font-semibold">{PROVIDER_NAME[provider]}</span>
                {limited[provider] ? <Badge tone="bad">out of quota</Badge> : null}
                <div className="flex-1">{modelControls(role, provider)}</div>
                <div className="flex items-center gap-0.5">
                  <button type="button" aria-label={`Move ${provider} earlier`} disabled={i === 0} onClick={() => move(i, -1)} className="rounded p-1 text-muted hover:bg-panel disabled:opacity-25"><ArrowUp className="size-3.5" /></button>
                  <button type="button" aria-label={`Move ${provider} later`} disabled={i === chain.length - 1} onClick={() => move(i, 1)} className="rounded p-1 text-muted hover:bg-panel disabled:opacity-25"><ArrowDown className="size-3.5" /></button>
                  <button type="button" aria-label={`Remove ${provider}`} disabled={chain.length === 1} onClick={() => setChain(kind, role, chain.filter((e) => e !== engine))} className="rounded p-1 text-muted hover:bg-panel disabled:opacity-25"><X className="size-3.5" /></button>
                </div>
              </li>
            );
          })}
        </ol>
        {rest.length ? (
          <div className="flex flex-wrap gap-2">
            {rest.map((engine) => (
              <Button key={engine} className="h-8 text-xs" onClick={() => setChain(kind, role, [...chain, engine])}>
                <Plus className="size-3.5" />Add {PROVIDER_NAME[providerOf(engine)]} as fallback
              </Button>
            ))}
          </div>
        ) : null}
        {rec ? (
          <div className="flex items-start gap-2 rounded-lg bg-accent-soft/40 p-2.5 text-xs">
            <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-accent-ink" aria-hidden />
            <div className="min-w-0 flex-1">
              <div><b>Suggested:</b> Codex <Mono>{rec.codex?.model}</Mono>{rec.codex?.effort ? ` · ${rec.codex.effort}` : ""} · Claude <Mono>{rec.claude?.model}</Mono></div>
              {rec.why ? <div className="text-muted">{rec.why}</div> : null}
            </div>
            <button type="button" className="shrink-0 font-semibold text-accent-ink hover:underline" onClick={() => setDraft({ ...draft, models: suggested(role, draft.models) })}>Use</button>
          </div>
        ) : null}
      </Card>
    );
  };

  const section = (kind: Kind, Icon: typeof Brain, title: string, text: string, roles: string[]) => (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <Icon className="size-4 self-center text-muted" aria-hidden />
        <h2 className="m-0 text-base font-semibold">{title}</h2>
        <span className="text-sm text-muted">{text}</span>
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">{roles.map((r) => roleCard(kind, r))}</div>
    </section>
  );

  return (
    <>
      <PageHeader title="AI providers" subtitle="As Platform Admin. For each role, the providers to use in order and the model each one runs. When a provider's quota runs out, the role moves to the next provider in the same job. Applies to the next AI call of every run." />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {d.status.map((s) => (
          <Card key={s.provider} className={cn("flex items-center gap-3 p-4", s.limited_until ? "border-bad/40" : "")}>
            <span className={cn("size-2.5 rounded-full", s.limited_until ? "bg-bad" : "bg-good")} aria-hidden />
            <div className="min-w-0 flex-1">
              <b className="text-base">{PROVIDER_NAME[s.provider]}</b>
              <div className="text-xs text-muted">{s.limited_until ? `Out of quota until ${new Date(s.limited_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Available"}</div>
            </div>
            {s.limited_until ? (
              <ActionButton success={`${PROVIDER_NAME[s.provider]} available again; waiting jobs run now`}
                title="The quota was reset (a new window or more credits): use it again now and run the jobs that waited for it"
                action={async () => store(await api<Providers>(`/api/admin/providers/${s.provider}/available`, { method: "POST" }))}>Mark available</ActionButton>
            ) : null}
          </Card>
        ))}
      </div>
      {section("thinking", Brain, "Thinking roles", "read-only sessions", d.thinking_roles)}
      {section("doing", Hammer, "Doing roles", "write tests and code; only the executors a project allows", d.doing_roles)}
      <Card className="flex flex-col gap-2 p-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={draft.fallback} onChange={(e) => setDraft({ ...draft, fallback: e.target.checked })} />
          <span>After the listed providers, also try any other provider rather than wait</span>
        </label>
        <p className="m-0 text-xs text-muted">Empty model: the CLI's default (Codex: ~/.codex/config.toml). Codex models are the ones your Codex login may use (~/.codex/models_cache.json). Claude takes opus, sonnet, haiku or a full model id. Eval runs keep their pinned engines.</p>
      </Card>
      <div className={cn("sticky bottom-0 z-10 flex flex-wrap items-center gap-2 rounded-xl border p-3 shadow-sm", dirty ? "border-accent/50 bg-panel" : "border-line bg-panel/90")}>
        <span className={cn("text-sm", dirty ? "font-semibold text-accent-ink" : "text-muted")}>{dirty ? "Unsaved changes" : "All changes saved"}</span>
        <span className="flex-1" />
        <Button onClick={() => setDraft({ ...draft, models: [...d.thinking_roles, ...d.doing_roles].reduce((m, r) => suggested(r, m), draft.models) })}>Apply all suggestions</Button>
        <Button disabled={!dirty} onClick={() => setDraft(pick(d))}>Discard</Button>
        <ActionButton variant="primary" disabled={!dirty} success="AI providers saved; the next AI calls use them"
          action={async () => store(await api<Providers>("/api/admin/providers", { method: "PUT", body: draft }))}>Save</ActionButton>
      </div>
      <datalist id="models-claude">{(d.model_suggestions.claude ?? []).map((n) => <option key={n} value={n} />)}</datalist>
    </>
  );
}
