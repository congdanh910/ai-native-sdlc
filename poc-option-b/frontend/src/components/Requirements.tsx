import { useMemo, useState } from "react";
import type { Citation, Doc, Inventory, RunDetail } from "../lib/api";
import { role, short } from "../lib/utils";
import { Badge, Card, Empty, Input, Mono, Select } from "./ui";

const TYPES: Record<string, string> = {
  goal: "Goals", actor: "Actors", permission: "Permissions", feature: "Features", function: "Functions", flow: "Flows",
  rule: "Business rules", data: "Data", integration: "Integrations", exception: "Exceptions", acceptance: "Acceptance",
  nfr: "Non-functional", constraint: "Constraints", option: "Deployment options", term: "Glossary",
};

/** A link to the cited place: a PDF opens on its page; other sources open as the stored file. */
function CiteLink({ run, inventory, cite }: { run: RunDetail; inventory: Inventory; cite: Citation }) {
  const source = run.sources?.find((s) => s.id === cite.source);
  const page = /(?:page|p)\s*(\d+)/i.exec(cite.location)?.[1];
  const href = `/api/runs/${run.id}/sources/${cite.source}/file${source?.kind === "pdf" && page ? `#page=${page}` : ""}`;
  return <a href={href} target="_blank" rel="noopener" className="whitespace-nowrap">{inventory.sources[cite.source] ?? short(cite.source)} · {cite.location}</a>;
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
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-sm">
          <thead><tr className="bg-side text-left text-xs text-muted">{["ID", "Type", "Name", "Requirement", "Of", "Phase", "Sources"].map((h) => <th key={h} className="border-b border-line px-3 py-2.5 font-semibold">{h}</th>)}</tr></thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className="align-top">
                <td className="border-b border-line px-3 py-2"><Mono>{i.id}</Mono>{i.source_ref ? <div className="text-xs text-muted">{i.source_ref}</div> : null}</td>
                <td className="border-b border-line px-3 py-2 text-xs text-muted">{TYPES[i.type] ?? i.type}</td>
                <td className="border-b border-line px-3 py-2 font-semibold">{i.name}{i.aliases.length ? <div className="text-xs font-normal text-muted">also: {i.aliases.join(", ")}</div> : null}</td>
                <td className="border-b border-line px-3 py-2">{i.text || <span className="text-muted">—</span>}</td>
                <td className="border-b border-line px-3 py-2 text-muted">{i.parent || "—"}</td>
                <td className="border-b border-line px-3 py-2 text-muted">{i.phase || "—"}</td>
                <td className="border-b border-line px-3 py-2 text-xs"><div className="flex flex-col gap-0.5">{i.sources.map((c, n) => <CiteLink key={n} run={run} inventory={inventory} cite={c} />)}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length ? <p className="m-0 p-4 text-sm text-muted">No item matches.</p> : null}
      </Card>
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
