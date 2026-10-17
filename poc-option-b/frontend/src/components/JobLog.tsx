import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { cn } from "../lib/utils";

interface LogChunk { text: string; offset: number; status: string }

const LIVE = ["queued", "running"];

/** The live log of one job: polls from the last offset every 2 seconds while the job is queued or running. */
export function JobLog({ runId, executionId, title, onClose }: { runId: string; executionId: string; title: string; onClose: () => void }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("running");
  const [error, setError] = useState<string | null>(null);
  const offset = useRef(0);
  const box = useRef<HTMLPreElement>(null);
  const follow = useRef(true);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    offset.current = 0;
    setText("");
    const poll = async () => {
      try {
        const chunk = await api<LogChunk>(`/api/runs/${runId}/executions/${executionId}/log?offset=${offset.current}`);
        if (stopped) return;
        offset.current = chunk.offset;
        if (chunk.text) setText((current) => current + chunk.text);
        setStatus(chunk.status);
        setError(null);
        if (LIVE.includes(chunk.status) || chunk.text) timer = setTimeout(poll, LIVE.includes(chunk.status) ? 2000 : 0);
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message);
        timer = setTimeout(poll, 5000);
      }
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [runId, executionId]);

  useEffect(() => {
    if (follow.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [text]);

  const live = LIVE.includes(status);
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <b>Log: {title}</b>
        <span className={cn("inline-flex items-center gap-1.5 text-xs", live ? "text-accent" : "text-muted")}>
          {live ? <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden /> : null}
          {live ? "live, refreshing every 2 s" : status}
        </span>
        {error ? <span className="text-xs text-bad">{error}</span> : null}
        <button type="button" className="ml-auto text-xs text-muted hover:text-ink" onClick={onClose}>Close</button>
      </div>
      <pre ref={box} onScroll={(e) => {
        const el = e.currentTarget;
        follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;  // stop following when the reader scrolls up
      }} className="m-0 max-h-[420px] min-h-24 overflow-auto rounded-md bg-sunken p-3 font-mono text-[12px] leading-[1.55] whitespace-pre-wrap text-ink-2">
        {text || (live ? "Waiting for the first line…" : "No log for this job. Jobs that ran before live logs were added have none.")}
      </pre>
    </div>
  );
}
