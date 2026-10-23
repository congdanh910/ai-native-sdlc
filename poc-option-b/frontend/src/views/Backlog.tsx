import { Fragment, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Play } from "lucide-react";
import { Link, useLocation } from "wouter";
import { ActionButton, Badge, Button, Card, ErrorBox, Input, Loading, Markdown, Mono, PageHeader, StatusBadge, Textarea } from "../components/ui";
import { api, ApiError, type RunSummary } from "../lib/api";
import { short } from "../lib/utils";

interface Item {
  story: string; depends_on: string[]; reason: string; hold: boolean; done: string;
  title: string; actors: string; feature: string; statement: string; in_srs: boolean; wave: number | null;
  state: "delivered" | "running" | "held" | "blocked" | "ready"; detail: string; run: { id: string; status: string } | null;
}
interface Backlog {
  project: string; commit: string | null; status: "draft" | "approved" | null; version: number; notes: string;
  approved_by: string | null; approved_at: string | null; srs_commit: string | null;
  delivery: { auto_start: boolean; max_open_runs: number | null; open_runs: number; config_valid: boolean };
  items: Item[]; not_in_backlog: { story: string; title: string; actors: string; feature: string }[]; problems: string[];
}
type Edit = Pick<Item, "story" | "depends_on" | "reason" | "hold" | "done">;

const STATE_TONE = { delivered: "good", running: "accent", held: "neutral", blocked: "warn", ready: "violet" } as const;
const pick = (i: Edit): Edit => ({ story: i.story, depends_on: i.depends_on, reason: i.reason, hold: i.hold, done: i.done });

/** The project's user stories in delivery order. The Tech Lead AI proposes the order and dependencies from the SRS and
 * SDS; the controller checks them and works out each story's state; the Tech Lead edits, approves and starts stories. */
export function BacklogView({ project }: { project: string }) {
  const client = useQueryClient();
  const [, navigate] = useLocation();
  const path = `/api/projects/${encodeURIComponent(project)}/backlog`;
  const backlog = useQuery({ queryKey: ["backlog", project], queryFn: () => api<Backlog>(path), refetchInterval: 15000 });
  const [edits, setEdits] = useState<Edit[] | null>(null);
  const [feedback, setFeedback] = useState("");
  const [reason, setReason] = useState("");
  const [opened, setOpened] = useState<string | null>(null);
  useEffect(() => { setEdits(null); }, [backlog.data?.version]);
  if (backlog.isLoading) return <Loading />;
  if (backlog.error) return <ErrorBox error={backlog.error} />;
  const data = backlog.data!;
  const rows = edits ?? data.items.map(pick);
  const byStory = new Map(data.items.map((i) => [i.story, i]));
  const ids = rows.map((r) => r.story);
  const dirty = edits !== null;
  const set = (index: number, change: Partial<Edit>) => setEdits(rows.map((r, i) => (i === index ? { ...r, ...change } : r)));
  const move = (index: number, by: number) => {
    const next = [...rows];
    const [row] = next.splice(index, 1);
    next.splice(index + by, 0, row);
    setEdits(next);
  };
  const store = (fresh: Backlog) => { client.setQueryData(["backlog", project], fresh); setEdits(null); };
  const counts = data.items.reduce<Record<string, number>>((acc, i) => ({ ...acc, [i.state]: (acc[i.state] ?? 0) + 1 }), {});
  return (
    <>
      <PageHeader eyebrow={<Link href="/projects">Projects</Link>} title={`Backlog · ${project}`}
        subtitle="As Tech Lead. Every user story of the SRS in the order to deliver it. A story opens only after every story it depends on is merged; the Tech Lead AI proposes the order from the SRS and SDS, the controller checks it."
        actions={data.status ? <Badge tone={data.status === "approved" ? "good" : "warn"} dot>{data.status === "approved" ? `approved by ${data.approved_by}` : `draft v${data.version}`}</Badge> : null} />
      <Card className="flex flex-col gap-3 p-5">
        <div className="flex flex-wrap gap-2 text-sm">
          {(["ready", "running", "blocked", "held", "delivered"] as const).map((s) => <Badge key={s} tone={STATE_TONE[s]}>{counts[s] ?? 0} {s}</Badge>)}
          <span className="text-muted">SRS at <Mono>{short(data.commit ?? "", 10)}</Mono>{data.srs_commit && data.srs_commit !== data.commit ? <> · backlog made at <Mono>{short(data.srs_commit, 10)}</Mono></> : null}</span>
        </div>
        <p className="m-0 text-sm text-muted">
          {!data.delivery.config_valid ? ".sdlc/project.yaml does not parse: stories do not open on their own."
            : data.delivery.auto_start
              ? <>Once approved, the next ready story opens on its own while fewer than <b>{data.delivery.max_open_runs}</b> feature runs are open (now {data.delivery.open_runs}); <Mono>delivery</Mono> in .sdlc/project.yaml. A story opens on its own once; after a run that did not merge, start it here.</>
              : <>Stories open only with Start (<Mono>delivery.auto_start: false</Mono>); {data.delivery.open_runs} feature run(s) open.</>}
        </p>
        <Textarea rows={2} placeholder={data.items.length ? "What should the AI change in the order? (optional)" : "Anything the AI should know about the order (optional)"} value={feedback} onChange={(e) => setFeedback(e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <ActionButton variant={data.items.length ? undefined : "primary"} disabled={dirty} title={dirty ? "Save or discard your edits first" : undefined}
            success="The Tech Lead AI ordered the stories; check the draft" action={async () => {
              store(await api<Backlog>(`${path}/propose`, { method: "POST", body: { feedback } }));
              setFeedback("");
            }}>{data.items.length ? "Propose again" : "Propose order"}</ActionButton>
          {dirty ? <>
            <ActionButton variant="primary" success="Saved as a draft" action={async () => store(await api<Backlog>(path, { method: "PUT", body: { items: rows, notes: data.notes, version: data.version } }))}>Save</ActionButton>
            <Button onClick={() => setEdits(null)}>Discard edits</Button>
          </> : null}
        </div>
        {data.notes ? <div className="rounded-lg border border-line bg-panel p-3 text-sm"><Markdown source={data.notes} /></div> : null}
        {data.problems.length ? (
          <div className="rounded-lg border border-bad bg-bad-soft p-3 text-sm text-bad">
            <b>Fix before approving</b>
            <ul className="m-0 mt-1 pl-5">{data.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        ) : null}
        {data.status === "draft" && !dirty ? (
          <div className="flex flex-wrap items-center gap-2">
            <Input className="min-w-[280px] flex-1" placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <ActionButton variant="primary" disabled={data.problems.length > 0} success="Backlog approved"
              action={async () => store(await api<Backlog>(`${path}/approve`, { method: "POST", body: { version: data.version, reason } }))}>Approve backlog</ActionButton>
          </div>
        ) : null}
      </Card>
      {rows.length ? (
        <Card className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th scope="col" className="p-2.5">#</th>
                <th scope="col" className="p-2.5">Wave</th>
                <th scope="col" className="p-2.5">Story</th>
                <th scope="col" className="p-2.5">State</th>
                <th scope="col" className="p-2.5">Depends on</th>
                <th scope="col" className="p-2.5">Hold / done outside</th>
                <th scope="col" className="p-2.5"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const info = byStory.get(row.story);
                return (
                  <Fragment key={row.story}>
                  <tr className={`align-top ${opened === row.story ? "" : "border-b border-line last:border-0"}`}>
                    <td className="p-2.5 text-muted">{index + 1}</td>
                    <td className="p-2.5">{info?.wave ? <Badge>{info.wave}</Badge> : <span className="text-muted">–</span>}</td>
                    <td className="max-w-[360px] p-2.5">
                      <div>
                        <button type="button" className="inline-flex items-start gap-1 text-left hover:text-accent-ink" aria-expanded={opened === row.story}
                          title="Show what the SRS and SDS say about this story, its runs and its accepted spec"
                          onClick={() => setOpened(opened === row.story ? null : row.story)}>
                          {opened === row.story ? <ChevronDown className="mt-0.5 size-4 shrink-0" aria-hidden /> : <ChevronRight className="mt-0.5 size-4 shrink-0" aria-hidden />}
                          <span><Mono>{row.story}</Mono> <b>{info?.title}</b></span>
                        </button>
                        {info && !info.in_srs ? <> <Badge tone="bad">not in the SRS</Badge></> : null}
                      </div>
                      <div className="text-xs text-muted">{info?.feature} · {info?.actors}</div>
                      {row.reason ? <div className="mt-1 text-xs text-ink-2">{row.reason}</div> : null}
                    </td>
                    <td className="p-2.5">
                      {info ? <>
                        <Badge tone={STATE_TONE[info.state]} dot>{info.state}</Badge>
                        <div className="mt-1 text-xs text-muted">{info.detail}</div>
                        {info.run ? <Link href={`/runs/${info.run.id}`} className="text-xs"><StatusBadge status={info.run.status} /></Link> : null}
                      </> : <span className="text-xs text-muted">saved after Save</span>}
                    </td>
                    <td className="p-2.5">
                      <Input aria-label={`Stories ${row.story} depends on`} className="w-44" list="backlog-stories" value={row.depends_on.join(", ")}
                        onChange={(e) => set(index, { depends_on: e.target.value.split(/[\s,]+/).filter(Boolean) })} />
                    </td>
                    <td className="p-2.5">
                      <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={row.hold} onChange={(e) => set(index, { hold: e.target.checked })} /> hold</label>
                      <Input aria-label={`Why ${row.story} was done outside the platform`} className="mt-1 w-44" placeholder="done outside: why" value={row.done}
                        onChange={(e) => set(index, { done: e.target.value })} />
                    </td>
                    <td className="whitespace-nowrap p-2.5">
                      <Button aria-label={`Move ${row.story} up`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button>
                      <Button aria-label={`Move ${row.story} down`} disabled={index === rows.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button>
                      {info?.state === "ready" && !dirty ? (
                        <ActionButton variant="primary" success={`${row.story} started`} title="Open the run of this story with outcomes drafted from the SRS and SDS; it stops at every gate"
                          action={async () => {
                            try {
                              const run = await api<RunSummary>(`${path}/${encodeURIComponent(row.story)}/start`, { method: "POST" });
                              navigate(`/runs/${run.id}`);
                            } catch (error) {
                              client.invalidateQueries({ queryKey: ["backlog", project] });
                              throw error instanceof ApiError ? error : new ApiError(String(error));
                            }
                          }}><Play className="size-3.5" /> Start</ActionButton>
                      ) : null}
                    </td>
                  </tr>
                  {opened === row.story ? (
                    <tr className="border-b border-line last:border-0">
                      <td colSpan={7} className="bg-sunken/40 p-3"><StoryDetail project={project} story={row.story} /></td>
                    </tr>
                  ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <datalist id="backlog-stories">{ids.map((id) => <option key={id} value={id} />)}</datalist>
        </Card>
      ) : null}
      {data.not_in_backlog.length ? (
        <Card className="flex flex-col gap-2 p-5">
          <h2 className="m-0 text-base font-semibold">Not in the backlog</h2>
          <p className="m-0 text-sm text-muted">User stories of the SRS the backlog does not place yet (new in the SRS, or no backlog yet). Add them at the end, or propose again.</p>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {data.not_in_backlog.map((s) => (
              <li key={s.story} className="flex flex-wrap items-center gap-2 text-sm">
                <Mono>{s.story}</Mono> {s.title} <span className="text-xs text-muted">{s.feature} · {s.actors}</span>
                {data.items.length ? <Button className="ml-auto" onClick={() => setEdits([...rows, { story: s.story, depends_on: [], reason: "", hold: false, done: "" }])}>Add at the end</Button> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </>
  );
}


interface Detail {
  story: string; srs: string; sds: string; spec_path: string; spec: string; needed_by: string[];
  requirements: { id: string; function: string; text: string; rule: string }[];
  apis: { id: string; purpose: string }[]; errors: { code: string; meaning: string }[];
  item: { depends_on: string[]; reason: string } | null;
  runs: { id: string; status: string; title: string; created_at: string }[];
}

/** One story opened in the backlog: what the SRS and SDS say about it, where it sits, its runs and, once delivered,
 * the accepted spec with its criteria. */
function StoryDetail({ project, story }: { project: string; story: string }) {
  const detail = useQuery({
    queryKey: ["backlog-story", project, story],
    queryFn: () => api<Detail>(`/api/projects/${encodeURIComponent(project)}/backlog/${encodeURIComponent(story)}`),
  });
  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorBox error={detail.error} />;
  const d = detail.data!;
  return (
    <div className="grid grid-cols-1 gap-4 text-sm lg:grid-cols-2">
      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-semibold">SRS.md</h3>
        {d.srs ? <div className="rounded-lg border border-line bg-panel p-3"><Markdown source={d.srs} /></div> : <p className="m-0 text-muted">No section for this story.</p>}
        {d.requirements.length ? (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {d.requirements.map((r) => (
              <li key={r.id}><Mono>{r.id}</Mono> <b>{r.function}</b>: {r.text}{r.rule ? <span className="block text-xs text-muted">Rule: {r.rule}</span> : null}</li>
            ))}
          </ul>
        ) : null}
        <h3 className="m-0 mt-2 text-sm font-semibold">SDS.md</h3>
        {d.sds ? <div className="rounded-lg border border-line bg-panel p-3"><Markdown source={d.sds} /></div> : <p className="m-0 text-muted">No section for this story.</p>}
        {d.apis.length ? <ul className="m-0 flex list-none flex-col gap-1 p-0">{d.apis.map((a) => <li key={a.id}><Mono>{a.id}</Mono> {a.purpose}</li>)}</ul> : null}
        {d.errors.length ? <div className="text-xs text-ink-2">Errors: {d.errors.map((e) => <span key={e.code} className="mr-3"><Mono>{e.code}</Mono>{e.meaning ? ` ${e.meaning}` : ""}</span>)}</div> : null}
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-semibold">In the backlog</h3>
        <div>Depends on: {d.item?.depends_on.length ? d.item.depends_on.map((s) => <Mono key={s} className="mr-2">{s}</Mono>) : <span className="text-muted">no other story</span>}</div>
        <div>Needed by: {d.needed_by.length ? d.needed_by.map((s) => <Mono key={s} className="mr-2">{s}</Mono>) : <span className="text-muted">no other story</span>}</div>
        {d.item?.reason ? <div className="text-ink-2">{d.item.reason}</div> : null}
        <h3 className="m-0 mt-2 text-sm font-semibold">Runs</h3>
        {d.runs.length ? (
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {[...d.runs].reverse().map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2"><Link href={`/runs/${r.id}`}><Mono>{r.id.slice(0, 8)}</Mono> {r.title}</Link> <StatusBadge status={r.status} /> <span className="text-xs text-muted">{new Date(r.created_at).toLocaleString()}</span></li>
            ))}
          </ul>
        ) : <p className="m-0 text-muted">Not started yet.</p>}
        {d.spec ? <>
          <h3 className="m-0 mt-2 text-sm font-semibold">Accepted spec <Mono className="font-normal text-muted">{d.spec_path}</Mono></h3>
          <div className="max-h-[480px] overflow-auto rounded-lg border border-line bg-panel p-3"><Markdown source={d.spec} /></div>
        </> : null}
      </section>
    </div>
  );
}
