import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { ActionButton, Badge, Button, Card, ErrorBox, Loading, Mono, PageHeader, Select } from "../components/ui";
import { api } from "../lib/api";
import { role as roleLabel } from "../lib/utils";

type Models = Record<string, Record<string, { model: string; effort: string }>>;
interface Providers {
  thinking: Record<string, string[]>; doing: Record<string, string[]>; fallback: boolean; models: Models;
  model_suggestions: Record<string, string[]>; efforts: string[];
  thinking_roles: string[]; doing_roles: string[]; thinking_engines: string[]; doing_engines: string[];
  env: Record<string, string>;
  status: { provider: string; limited_until: string | null; reason: string }[];
}

/** Platform Admin: each role's engines in order of preference. A provider out of its subscription window is marked
 * limited and the role moves to its next engine in the same job; Mark available when the quota was reset. */
export function ProvidersView() {
  const client = useQueryClient();
  const data = useQuery({ queryKey: ["providers"], queryFn: () => api<Providers>("/api/admin/providers"), refetchInterval: 20000 });
  const [draft, setDraft] = useState<Pick<Providers, "thinking" | "doing" | "fallback" | "models"> | null>(null);
  useEffect(() => { if (data.data && !draft) setDraft({ thinking: data.data.thinking, doing: data.data.doing, fallback: data.data.fallback, models: data.data.models }); }, [data.data, draft]);
  if (data.isLoading || !draft) return <Loading />;
  if (data.error) return <ErrorBox error={data.error} />;
  const d = data.data!;
  const store = (fresh: Providers) => { client.setQueryData(["providers"], fresh); setDraft({ thinking: fresh.thinking, doing: fresh.doing, fallback: fresh.fallback, models: fresh.models }); };
  const choice = (role: string, provider: string) => draft.models[role]?.[provider] ?? { model: "", effort: "" };
  const setChoice = (role: string, provider: string, change: Partial<{ model: string; effort: string }>) =>
    setDraft({ ...draft, models: { ...draft.models, [role]: { ...(draft.models[role] ?? {}), [provider]: { ...choice(role, provider), ...change } } } });
  const modelCells = (r: string) => ["codex", "claude"].map((provider) => (
    <td key={provider} className="p-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <input aria-label={`${r} model on ${provider}`} list={`models-${provider}`} placeholder="default"
          className="h-8 w-40 rounded-md border border-line bg-panel px-2 font-mono text-[12.5px]"
          value={choice(r, provider).model} onChange={(e) => setChoice(r, provider, { model: e.target.value })} />
        {provider === "codex" ? (
          <Select aria-label={`${r} reasoning effort on codex`} className="h-8 w-auto text-sm" value={choice(r, provider).effort}
            onChange={(e) => setChoice(r, provider, { effort: e.target.value })}>
            <option value="">effort: default</option>
            {d.efforts.map((e) => <option key={e} value={e}>{e}</option>)}
          </Select>
        ) : null}
      </div>
    </td>
  ));
  const setList = (kind: "thinking" | "doing", role: string, list: string[]) => setDraft({ ...draft, [kind]: { ...draft[kind], [role]: list } });
  const rows = (kind: "thinking" | "doing", roles: string[], engines: string[], fallbackFirst: (role: string) => string) => roles.map((r) => {
    const list = draft[kind][r] ?? [];
    const shown = list.length ? list : [fallbackFirst(r)];
    const rest = engines.filter((e) => !list.includes(e));
    return (
      <tr key={r} className="border-b border-line align-top last:border-0">
        <td className="p-2.5 font-medium">{roleLabel(r)}</td>
        <td className="p-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            {list.length ? list.map((e, i) => (
              <span key={e} className="inline-flex items-center gap-1 rounded-full border border-line bg-panel py-0.5 pl-2.5 pr-1 text-sm">
                <span className="text-xs text-muted">{i + 1}.</span><Mono>{e}</Mono>
                <button type="button" aria-label={`Move ${e} earlier`} disabled={i === 0} className="p-0.5 disabled:opacity-30" onClick={() => { const n = [...list]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; setList(kind, r, n); }}><ArrowUp className="size-3" /></button>
                <button type="button" aria-label={`Move ${e} later`} disabled={i === list.length - 1} className="p-0.5 disabled:opacity-30" onClick={() => { const n = [...list]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; setList(kind, r, n); }}><ArrowDown className="size-3" /></button>
                <button type="button" aria-label={`Remove ${e}`} className="p-0.5" onClick={() => setList(kind, r, list.filter((x) => x !== e))}><X className="size-3" /></button>
              </span>
            )) : <span className="text-sm text-muted">as configured: <Mono>{shown[0]}</Mono></span>}
            {rest.length ? (
              <Select aria-label={`Add an engine for ${r}`} className="h-8 w-auto text-sm" value="" onChange={(e) => e.target.value && setList(kind, r, [...list, e.target.value])}>
                <option value="">+ add</option>
                {rest.map((e) => <option key={e} value={e}>{e}</option>)}
              </Select>
            ) : null}
          </div>
        </td>
        {modelCells(r)}
      </tr>
    );
  });
  const head = (
    <thead><tr className="text-left text-xs text-muted">
      <th scope="col" className="p-2.5">Role</th><th scope="col" className="p-2.5">Engines in order</th>
      <th scope="col" className="p-2.5">Model on Codex</th><th scope="col" className="p-2.5">Model on Claude</th>
    </tr></thead>
  );
  return (
    <>
      <PageHeader title="AI providers" subtitle="As Platform Admin. Each role's engines in order of preference. When a provider's subscription window is used up, the role moves to its next engine in the same job; it waits only when every engine is out of quota." />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {d.status.map((s) => (
          <Card key={s.provider} className="flex flex-col gap-2 p-4">
            <div className="flex items-center gap-2">
              <b className="text-base capitalize">{s.provider}</b>
              {s.limited_until ? <Badge tone="bad" dot>out of quota until {new Date(s.limited_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</Badge> : <Badge tone="good" dot>available</Badge>}
              {s.limited_until ? (
                <ActionButton className="ml-auto" success={`${s.provider} available again; waiting jobs run now`}
                  title="The quota was reset (a new window or more credits): use it again now and run the jobs that waited for it"
                  action={async () => store(await api<Providers>(`/api/admin/providers/${s.provider}/available`, { method: "POST" }))}>Mark available</ActionButton>
              ) : null}
            </div>
            {s.reason ? <p className="m-0 text-xs text-muted">{s.reason.slice(-240)}</p> : null}
          </Card>
        ))}
      </div>
      <Card className="flex flex-col gap-3 p-5">
        <h2 className="m-0 text-base font-semibold">Thinking roles</h2>
        <p className="m-0 text-sm text-muted">Read-only sessions that write specs, plans, test cases, reviews and briefs. Empty: the engine of SDLC_ENGINES in .env.</p>
        <div className="overflow-x-auto"><table className="w-full border-collapse">{head}<tbody>{rows("thinking", d.thinking_roles, d.thinking_engines, (r) => d.env[r] ?? "")}</tbody></table></div>
        <h2 className="m-0 mt-2 text-base font-semibold">Doing roles</h2>
        <p className="m-0 text-sm text-muted">Sessions that write tests and code in the run's worktree. Only the executors a project allows in its .sdlc/project.yaml are used. Empty: the project's qa_executor / default_executor.</p>
        <div className="overflow-x-auto"><table className="w-full border-collapse">{head}<tbody>{rows("doing", d.doing_roles, d.doing_engines, () => "the project's executor")}</tbody></table></div>
        <p className="m-0 text-xs text-muted">Model empty: the CLI's default (Codex: ~/.codex/config.toml; Claude: its configured model). Claude takes aliases such as opus, sonnet, haiku or a full model id; Codex takes its model names and a reasoning effort.</p>
        {Object.entries(d.model_suggestions).map(([provider, names]) => <datalist key={provider} id={`models-${provider}`}>{names.map((n) => <option key={n} value={n} />)}</datalist>)}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={draft.fallback} onChange={(e) => setDraft({ ...draft, fallback: e.target.checked })} />
          After the listed engines, fall back to any other provider (Codex, Claude) rather than wait
        </label>
        <div className="flex gap-2">
          <ActionButton variant="primary" success="AI providers saved; the next calls use them" action={async () => store(await api<Providers>("/api/admin/providers", { method: "PUT", body: draft }))}>Save</ActionButton>
          <Button onClick={() => setDraft({ thinking: d.thinking, doing: d.doing, fallback: d.fallback, models: d.models })}>Discard</Button>
        </div>
      </Card>
    </>
  );
}
