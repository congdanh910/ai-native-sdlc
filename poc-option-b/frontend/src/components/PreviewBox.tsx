import { ExternalLink, Play, Square } from "lucide-react";
import { api, type RunDetail } from "../lib/api";
import { ActionButton, Badge, Card, Mono } from "./ui";

const TONE = { running: "good", starting: "accent", stopping: "warn", stopped: "neutral", failed: "bad", unavailable: "neutral" } as const;

/** The run's preview: the application of its branch on 127.0.0.1, with the demo accounts, for people to try. */
export function PreviewBox({ run, onChange }: { run: RunDetail; onChange: () => void }) {
  const preview = run.preview;
  if (!preview || !preview.available) return null;
  const act = async (action: "start" | "stop") => {
    await api(`/api/runs/${run.id}/preview`, { method: "POST", body: { action } });
    onChange();
  };
  const busy = preview.status === "starting" || preview.status === "stopping";
  return (
    <Card className="flex flex-col gap-2 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <b>Preview</b>
        <Badge tone={TONE[preview.status] ?? "neutral"} dot>{preview.status}</Badge>
        {preview.status === "running" && preview.url ? (
          <a className="inline-flex items-center gap-1 font-semibold text-accent-ink" href={preview.url} target="_blank" rel="noreferrer">
            Try it <ExternalLink className="size-3.5" /></a>
        ) : null}
        {preview.expires_at && preview.status === "running" ? (
          <span className="text-xs text-muted">stops at {new Date(preview.expires_at).toLocaleTimeString()}</span>
        ) : null}
        <span className="ml-auto flex gap-2">
          {preview.status === "unavailable" ? null : preview.status === "running" || busy ? (
            <ActionButton disabled={preview.status === "stopping"} success="Stopping the preview" action={() => act("stop")}>
              <Square className="size-3.5" /> Stop</ActionButton>
          ) : (
            <ActionButton variant="primary" success="Starting the preview; building can take a few minutes" action={() => act("start")}>
              <Play className="size-3.5" /> Start preview</ActionButton>
          )}
        </span>
      </div>
      {preview.status !== "running" && preview.detail ? (
        <p className={`m-0 whitespace-pre-wrap text-xs ${preview.status === "failed" ? "text-bad" : "text-muted"}`}>{preview.detail.slice(0, 1200)}</p>
      ) : null}
      {preview.accounts.length ? (
        <table className="w-full text-xs">
          <thead><tr className="text-left text-muted"><th className="py-1 pr-3">Role</th><th className="pr-3">User</th><th className="pr-3">Password</th><th>Note</th></tr></thead>
          <tbody>
            {preview.accounts.map((a) => (
              <tr key={a.username} className="border-t border-line">
                <td className="py-1 pr-3">{a.role}</td><td className="pr-3"><Mono>{a.username}</Mono></td>
                <td className="pr-3"><Mono>{a.password}</Mono></td><td className="text-muted">{a.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}
