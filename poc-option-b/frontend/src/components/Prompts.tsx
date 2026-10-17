import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../lib/api";
import { Button, Card, Mono } from "./ui";

interface PromptList { prompts: { name: string; size: number }[]; prompt_version: string | null }

/** The exact prompts one job sent: the role's prompt from langflow/prompts plus the run's input JSON. */
export function JobPrompts({ runId, executionId, title, onClose }: { runId: string; executionId: string; title: string; onClose: () => void }) {
  const list = useQuery({ queryKey: ["prompts", executionId], queryFn: () => api<PromptList>(`/api/runs/${runId}/executions/${executionId}/prompts`) });
  const [chosen, setChosen] = useState<string | null>(null);
  const name = chosen ?? list.data?.prompts[0]?.name ?? null;
  const text = useQuery({
    queryKey: ["prompt", executionId, name], enabled: !!name,
    queryFn: () => api<{ text: string }>(`/api/runs/${runId}/executions/${executionId}/prompts?name=${encodeURIComponent(name ?? "")}`),
  });
  const prompts = list.data?.prompts ?? [];
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <b>Prompts: {title}</b>
        {list.data?.prompt_version ? <span className="text-xs text-muted">prompt version <Mono>{list.data.prompt_version}</Mono></span> : null}
        <button type="button" className="ml-auto text-xs text-muted hover:text-ink" onClick={onClose}>Close</button>
      </div>
      {list.isLoading ? <span className="text-sm text-muted">Loading…</span> : !prompts.length ? (
        <span className="text-sm text-muted">This job sent no prompt, or it ran before prompts were kept (17/10/2026).</span>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5">
            {prompts.map((p) => (
              <button key={p.name} type="button" onClick={() => setChosen(p.name)}
                className={`rounded-md border px-2 py-1 text-xs ${p.name === name ? "border-accent bg-accent-soft text-accent-ink" : "border-line hover:bg-side"}`}>
                {p.name.replace(/\.md$/, "")} <span className="text-muted">· {Math.round(p.size / 1000)} KB</span>
              </button>
            ))}
            {text.data ? <Button className="ml-auto h-7 px-2 text-xs" onClick={async () => {
              try { await navigator.clipboard.writeText(text.data.text); toast.success("Prompt copied"); } catch { toast.error("Copy failed"); }
            }}>Copy</Button> : null}
          </div>
          <span className="text-xs text-muted">The role's instructions come from langflow/prompts/ in the platform repository, followed by the input JSON built from this run only. Project knowledge (AGENTS.md, constitution, REVIEW.md) is inside the JSON.</span>
          <pre className="m-0 max-h-[520px] overflow-auto rounded-md bg-sunken p-3 font-mono text-[12px] whitespace-pre-wrap">{text.isLoading ? "Loading…" : text.data?.text}</pre>
        </>
      )}
    </Card>
  );
}
