import { useMemo, useState } from "react";
import type { Citation, Doc, Inventory, InventoryItem, RunDetail } from "../lib/api";
import { role, short } from "../lib/utils";
import { Badge, Card, Empty, Input, Mono, Select } from "./ui";

const TYPES: Record<string, string> = {
  goal: "Goals", actor: "Actors", permission: "Permissions", feature: "Features", function: "Functions", flow: "Flows",
  rule: "Business rules", data: "Data", integration: "Integrations", exception: "Exceptions", acceptance: "Acceptance",
  nfr: "Non-functional", constraint: "Constraints", option: "Deployment options", term: "Glossary",
};

/** A link to the cited place: a PDF opens on its page; other sources open as the stored file. The file name shows
 * only when the run has more than one source. */
function CiteLink({ run, inventory, cite }: { run: RunDetail; inventory: Inventory; cite: Citation }) {
  if (cite.source.startsWith("decision:")) return <Badge tone="accent">{cite.source.slice(9)}</Badge>;
  const source = run.sources?.find((s) => s.id === cite.source);
  const page = /(?:page|p)\s*(\d+)/i.exec(cite.location)?.[1];
  const href = `/api/runs/${run.id}/sources/${cite.source}/file${source?.kind === "pdf" && page ? `#page=${page}` : ""}`;
  const name = inventory.sources[cite.source] ?? short(cite.source);
  const many = Object.keys(inventory.sources).length > 1;
  const label = page ? `p. ${page}` : cite.location;
  return (
    <a href={href} target="_blank" rel="noopener" title={`${name} · ${cite.location}`}
      className="inline-flex max-w-[16rem] items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-xs hover:bg-side">
      {many ? <span className="truncate text-muted">{name.replace(/\.[a-z0-9]+$/i, "")}</span> : null}
      <span className="whitespace-nowrap">{label}</span>
    </a>
  );
}

function ItemRow({ run, inventory, item }: { run: RunDetail; inventory: Inventory; item: InventoryItem }) {
  const [open, setOpen] = useState(false);
  const long = item.text.length > 260;
  const meta = [
    item.parent ? ["Of", item.parent] : null, item.phase ? ["Phase", item.phase] : null,
    item.source_ref ? ["Ref", item.source_ref] : null, item.aliases.length ? ["Also", item.aliases.join(", ")] : null,
  ].filter(Boolean) as [string, string][];
  return (
    <li className="flex list-none flex-col gap-1.5 border-b border-line px-4 py-3 last:border-0">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <Mono className="text-xs text-muted">{item.id}</Mono>
        <b className="min-w-0 flex-1">{item.name}</b>
        {item.status !== "proposed" ? <Badge tone={item.status === "out_of_scope" ? "neutral" : item.status === "confirmed" ? "good" : "warn"}>{item.status.replace(/_/g, " ")}</Badge> : null}
        <span className="flex flex-wrap gap-1">{item.sources.map((c, n) => <CiteLink key={n} run={run} inventory={inventory} cite={c} />)}</span>
      </div>
      {item.text ? (
        <p className={`m-0 text-sm leading-relaxed text-ink-2 ${long && !open ? "line-clamp-3" : ""}`}>
          {item.text}
        </p>
      ) : null}
      {long ? <button type="button" className="self-start text-xs text-accent-ink hover:underline" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button> : null}
      {meta.length ? (
        <div className="flex flex-wrap gap-1.5 text-xs">
          {meta.map(([k, v]) => <span key={k} className="rounded-md bg-side px-1.5 py-0.5"><span className="text-muted">{k}:</span> {v}</span>)}
        </div>
      ) : null}
    </li>
  );
}

/** The latest requirement inventory: every item with the place it comes from, the checks, and the open questions. */
export function RequirementsTab({ run }: { run: RunDetail }) {
  const versions = run.documents.filter((d) => d.kind === "inventory");
  const [chosen, setChosen] = useState<number | null>(null);
  const doc: Doc | undefined = versions.find((d) => d.version === chosen) ?? versions[versions.length - 1];
  const [type, setType] = useState("");
  const [query, setQuery] = useState("");
  const inventory = doc?.inventory;
  const items = useMemo(() => (inventory?.items ?? []).filter((i) => (!type || i.type === type)
    && (!query || `${i.id} ${i.name} ${i.text} ${i.parent} ${i.aliases.join(" ")}`.toLowerCase().includes(query.toLowerCase()))), [inventory, type, query]);
  if (!doc || !inventory) return <Empty>No requirement inventory yet. Add the sources, then start the extraction.</Empty>;
  const counts = inventory.items.reduce<Record<string, number>>((acc, i) => ({ ...acc, [i.type]: (acc[i.type] ?? 0) + 1 }), {});
  const cited = inventory.items.filter((i) => i.sources.length).length;
  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <b>Inventory v{doc.version}</b>
          <span className="text-muted">by {role(doc.author)} · {inventory.items.length} items · {inventory.gaps.length} questions · {inventory.parts.length} part(s) read</span>
          {versions.length > 1 ? (
            <Select aria-label="Version" className="ml-auto h-8 w-auto" value={doc.version} onChange={(e) => setChosen(Number(e.target.value))}>
              {versions.map((d) => <option key={d.id} value={d.version}>v{d.version}</option>)}
            </Select>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge tone={cited === inventory.items.length ? "good" : "bad"}>{cited}/{inventory.items.length} items cite a source</Badge>
          {inventory.coarse_citations ? <Badge tone="warn">{inventory.coarse_citations} cite only a page range</Badge> : null}
          {inventory.problems.length ? <Badge tone="bad">{inventory.problems.length} problem(s)</Badge> : <Badge tone="good">no problems</Badge>}
          {doc.commit_sha ? <span className="text-muted">committed <Mono>{short(doc.commit_sha)}</Mono></span> : null}
        </div>
        {inventory.problems.length ? <ul className="m-0 pl-5 text-sm text-bad">{inventory.problems.map((p) => <li key={p}>{p}</li>)}</ul> : null}
        <div className="flex flex-wrap gap-2">
          <Select aria-label="Type" className="h-8 w-auto" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All types ({inventory.items.length})</option>
            {Object.entries(TYPES).filter(([k]) => counts[k]).map(([k, label]) => <option key={k} value={k}>{label} ({counts[k]})</option>)}
          </Select>
          <Input aria-label="Search" className="h-8 max-w-xs" placeholder="Search name, text, feature…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </Card>
      {Object.keys(TYPES).filter((kind) => items.some((i) => i.type === kind)).map((kind) => {
        const group = items.filter((i) => i.type === kind);
        return (
          <Card key={kind} className="overflow-hidden">
            <details open={!!type || !!query || group.length <= 40}>
              <summary className="flex cursor-pointer items-center gap-2 border-b border-line bg-side px-4 py-2.5 text-sm font-semibold">
                {TYPES[kind]}<span className="font-normal text-muted">{group.length}</span>
              </summary>
              <ul className="m-0 p-0">{group.map((i) => <ItemRow key={i.id} run={run} inventory={inventory} item={i} />)}</ul>
            </details>
          </Card>
        );
      })}
      {!items.length ? <Empty>No item matches.</Empty> : null}
      {inventory.gaps.length ? (
        <Card className="flex flex-col gap-2 p-4">
          <h3 className="m-0 text-base font-semibold">Open questions ({inventory.gaps.length})</h3>
          <ul className="m-0 flex flex-col gap-2 pl-0">
            {inventory.gaps.map((g, n) => (
              <li key={n} className="flex flex-col gap-0.5 border-b border-line pb-2 last:border-0 list-none">
                <span className="flex flex-wrap items-center gap-2"><Badge>{g.kind}</Badge>{g.blocking ? <Badge tone="bad">blocking</Badge> : null}<b>{g.question}</b></span>
                {g.why ? <span className="text-xs text-muted">{g.why}</span> : null}
                {g.items.length ? <span className="text-xs text-muted">about {g.items.join(", ")}</span> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
