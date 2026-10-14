import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { Activity, BarChart3, FlaskConical, FolderGit2, Inbox, Moon, Play, Plus, Search, Sun, Workflow } from "lucide-react";
import { api, type Check, type InboxItem, type Meta, type RunSummary } from "../lib/api";
import { ACTION_LABEL, cn, role } from "../lib/utils";
import { Kbd } from "./ui";

const NAV = [
  { href: "/", label: "Inbox", icon: Inbox, match: (p: string) => p === "/" || p.startsWith("/inbox") },
  { href: "/new", label: "New request", icon: Plus, match: (p: string) => p.startsWith("/new") },
  { href: "/runs", label: "Runs", icon: Play, match: (p: string) => p.startsWith("/runs") },
  { href: "/projects", label: "Projects", icon: FolderGit2, match: (p: string) => p.startsWith("/projects") },
  { href: "/evals", label: "Evals", icon: FlaskConical, match: (p: string) => p.startsWith("/evals") },
  { href: "/reports", label: "Reports", icon: BarChart3, match: (p: string) => p.startsWith("/reports") },
  { href: "/health", label: "Health", icon: Activity, match: (p: string) => p.startsWith("/health") },
];

const ENGINE_ROWS: [string, string[]][] = [
  ["BA", ["business_analyst"]],
  ["Tech Lead · QA Analyst", ["tech_lead", "qa_analyst"]],
  ["Code Reviewer", ["code_reviewer"]],
];

function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem("theme");
      return saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch { /* private mode */ }
  }, [dark]);
  return [dark, setDark] as const;
}

export function useInbox() {
  return useQuery({ queryKey: ["inbox"], queryFn: () => api<InboxItem[]>("/api/inbox"), refetchInterval: 5000 });
}

function useInboxNotifications(items: InboxItem[] | undefined) {
  const last = useRef<number | null>(null);
  useEffect(() => {
    if (!items) return;
    document.title = items.length ? `(${items.length}) SDLC Control Room` : "SDLC Control Room";
    if (last.current !== null && items.length > last.current && "Notification" in window && Notification.permission === "granted") {
      const item = items[items.length - 1];
      new Notification("The team is waiting for you", { body: `${item.title}: ${ACTION_LABEL[item.action] ?? item.action}` });
    }
    last.current = items.length;
  }, [items]);
}

function TeamCard() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/api/meta"), staleTime: 60_000 });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => fetch("/health/ready").then((r) => r.json() as Promise<{ status: string; checks: Check[] }>),
    refetchInterval: 30_000,
  });
  const engines = meta.data?.engines ?? {};
  const ok = health.data?.status === "ok";
  return (
    <div className="mt-auto flex flex-col gap-2 rounded-xl border border-line bg-panel p-3 text-xs">
      <div className="font-semibold text-ink-2">Team</div>
      {ENGINE_ROWS.map(([label, roles]) => (
        <div key={label} className="flex flex-col">
          <span className="text-muted">{label}</span>
          <span className="font-mono text-ink">{roles.map((r) => engines[r] ?? "qwen3").filter((v, i, a) => a.indexOf(v) === i).join(", ")}</span>
        </div>
      ))}
      <div className="flex flex-col">
        <span className="text-muted">QA Engineer · Developer</span>
        <span className="font-mono text-ink">{meta.data?.doing_mode === "cli" ? "codex" : "mock"}</span>
      </div>
      <Link href="/health" className={cn("mt-1 flex items-center gap-1.5 font-semibold", ok ? "text-good" : "text-bad")}>
        <span className={cn("size-[7px] rounded-full", ok ? "bg-good" : "bg-bad")} aria-hidden />
        {health.data ? (ok ? "All services ok" : `Health: ${health.data.status}`) : "Checking health…"}
      </Link>
      <div className="text-muted">{meta.data?.operator}</div>
    </div>
  );
}

function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [, navigate] = useLocation();
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/api/runs"), enabled: open });
  const go = (href: string) => { onOpenChange(false); navigate(href); };
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[12vh]" onMouseDown={() => onOpenChange(false)}>
      <Command
        label="Go to"
        className="w-full max-w-xl overflow-hidden rounded-xl border border-line bg-panel shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onOpenChange(false); }}
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search className="size-4 text-muted" aria-hidden />
          <Command.Input autoFocus placeholder="Go to a run, page or project…" className="h-12 flex-1 bg-transparent text-sm outline-none" />
          <Kbd>esc</Kbd>
        </div>
        <Command.List className="max-h-[50vh] overflow-y-auto p-2">
          <Command.Empty className="p-4 text-sm text-muted">Nothing matches.</Command.Empty>
          <Command.Group heading="Pages" className="text-xs text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
            {NAV.map((item) => (
              <Command.Item key={item.href} value={`page ${item.label}`} onSelect={() => go(item.href)}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-sm text-ink data-[selected=true]:bg-sunken">
                <item.icon className="size-4 text-muted" aria-hidden />{item.label}
              </Command.Item>
            ))}
          </Command.Group>
          <Command.Group heading="Runs" className="text-xs text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
            {(runs.data ?? []).filter((r) => !r.experiment).slice(0, 30).map((run) => (
              <Command.Item key={run.id} value={`run ${run.title} ${run.project} ${run.id}`} onSelect={() => go(`/runs/${run.id}`)}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-sm text-ink data-[selected=true]:bg-sunken">
                <Workflow className="size-4 text-muted" aria-hidden />
                <span className="flex-1 truncate">{run.title}</span>
                <span className="font-mono text-xs text-muted">{run.project} · {run.id.slice(0, 8)}</span>
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [path] = useLocation();
  const [dark, setDark] = useTheme();
  const [palette, setPalette] = useState(false);
  const inbox = useInbox();
  useInboxNotifications(inbox.data);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const count = inbox.data?.length ?? 0;
  return (
    <div className="flex min-h-full flex-wrap">
      <aside className="flex max-w-[260px] flex-[1_1_232px] flex-col gap-5 border-r border-line bg-side px-3.5 py-5">
        <div className="flex items-center gap-2.5 px-1.5">
          <div className="flex size-7 items-center justify-center rounded-lg bg-ink text-panel" aria-hidden>
            <Workflow className="size-4" />
          </div>
          <div className="font-bold">SDLC Control Room</div>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-0.5">
          {NAV.map((item) => {
            const active = item.match(path);
            return (
              <Link key={item.href} href={item.href}
                className={cn("flex items-center gap-2.5 rounded-lg px-2.5 py-2 font-medium",
                  active ? "bg-accent-soft text-accent-ink" : "text-ink-2 hover:bg-sunken hover:text-ink")}>
                <item.icon className="size-4" aria-hidden />
                {item.label}
                {item.href === "/" && count ? (
                  <span className="ml-auto rounded-full bg-accent px-2 text-xs font-semibold text-white">{count}</span>
                ) : null}
              </Link>
            );
          })}
        </nav>
        <TeamCard />
      </aside>
      <main className="min-w-0 flex-[999_1_560px] px-8 pt-6 pb-16">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-6">
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={() => setPalette(true)}
              className="flex h-10 flex-[1_1_320px] items-center gap-2.5 rounded-xl border border-line-strong bg-panel px-3 text-left text-muted hover:border-accent">
              <Search className="size-4" aria-hidden />
              <span className="flex-1">Go to a run, page or project…</span>
              <Kbd>⌘K</Kbd>
            </button>
            <button type="button" aria-label={dark ? "Use the light theme" : "Use the dark theme"} onClick={() => setDark(!dark)}
              className="flex size-10 items-center justify-center rounded-xl border border-line-strong bg-panel text-ink-2 hover:bg-sunken">
              {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
            </button>
            <Link href="/new" className="inline-flex h-10 items-center gap-2 rounded-xl bg-ink px-4 font-semibold text-panel hover:text-panel hover:opacity-90">
              <Plus className="size-4" aria-hidden />New request
            </Link>
          </div>
          {children}
        </div>
      </main>
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </div>
  );
}

export function roleLabel(value: string) {
  return role(value);
}
