import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleDashed, XCircle } from "lucide-react";
import { ActionButton, Card, ErrorBox, Field, Input, Loading, Mono, PageHeader, StatusBadge } from "../components/ui";
import { api, ApiError, type Project } from "../lib/api";
import { short } from "../lib/utils";
import { Link } from "wouter";

const ICON = { ok: CheckCircle2, warn: CircleAlert, fail: XCircle, pending: CircleDashed } as const;
const COLOR = { ok: "text-good", warn: "text-warn", fail: "text-bad", pending: "text-muted" } as const;

export function ProjectsView() {
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
  const [form, setForm] = useState({ key: "", repo_url: "", branch: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  if (projects.isLoading) return <Loading />;
  if (projects.error) return <ErrorBox error={projects.error} />;
  const refresh = () => client.invalidateQueries({ queryKey: ["projects"] });
  return (
    <>
      <PageHeader title="Projects" subtitle="As Platform Admin. A project needs AGENTS.md, CLAUDE.md and .sdlc/project.yaml; the check runs its validation in the toolchain container." />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {(projects.data ?? []).map((p) => (
          <Card key={p.key} className="flex flex-col gap-3 p-5">
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 className="m-0 text-lg font-semibold">{p.key}</h2>
              <StatusBadge status={p.readiness} />
              <Link href={`/projects/${p.key}/backlog`} className="ml-auto text-sm font-semibold">Backlog</Link>
              <ActionButton success={`${p.key} checked`} action={async () => { await api(`/api/projects/${p.key}/check`, { method: "POST" }); refresh(); }}>
                Check again
              </ActionButton>
            </div>
            <div className="text-sm text-muted"><Mono>{p.repo_url}</Mono> · {p.default_branch}{p.checked_commit ? <> at <Mono>{short(p.checked_commit, 10)}</Mono></> : null}</div>
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
              {p.report.map((item) => {
                const key = (item.status in ICON ? item.status : "pending") as keyof typeof ICON;
                const Icon = ICON[key];
                return (
                  <li key={item.name} className="flex gap-2.5 text-sm">
                    <Icon className={`mt-0.5 size-4 shrink-0 ${COLOR[key]}`} aria-label={item.status} />
                    <span className="w-44 shrink-0 font-medium">{item.name}</span>
                    <span className="min-w-0 text-muted">{item.detail}</span>
                  </li>
                );
              })}
            </ul>
          </Card>
        ))}
      </div>
      <Card className="flex flex-col gap-4 p-5">
        <h2 className="m-0 text-lg font-semibold">Add a project</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Field label="Key" hint="equals project: in .sdlc/project.yaml" htmlFor="p-key" required error={errors.key}>
            <Input id="p-key" placeholder="inventory" value={form.key} aria-invalid={errors.key ? true : undefined}
              onChange={(e) => setForm({ ...form, key: e.target.value })} />
          </Field>
          <Field label="Repository" htmlFor="p-url" required error={errors.repo_url}>
            <Input id="p-url" placeholder="https://github.com/owner/repo" value={form.repo_url} aria-invalid={errors.repo_url ? true : undefined}
              onChange={(e) => setForm({ ...form, repo_url: e.target.value })} />
          </Field>
          <Field label="Branch" hint="empty = default branch" htmlFor="p-branch">
            <Input id="p-branch" placeholder="main" value={form.branch} onChange={(e) => setForm({ ...form, branch: e.target.value })} />
          </Field>
        </div>
        <div>
          <ActionButton variant="primary" success="Project added and checked" action={async () => {
            const local: Record<string, string> = {};
            const key = form.key.trim(), repo = form.repo_url.trim();
            if (!key) local.key = "Key is required";
            else if (!/^[a-z][a-z0-9-]{1,39}$/.test(key)) local.key = "Use 2-40 lowercase letters, digits or '-', starting with a letter";
            if (!repo) local.repo_url = "Repository is required: a GitHub URL or an absolute local path";
            setErrors(local);
            if (Object.keys(local).length) throw new ApiError("Fix the highlighted fields first.");
            try {
              await api("/api/projects", { method: "POST", body: { key, repo_url: repo, branch: form.branch.trim() || null } });
            } catch (error) {
              if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
              throw error;
            }
            setForm({ key: "", repo_url: "", branch: "" });
            refresh();
          }}>Add project</ActionButton>
        </div>
      </Card>
    </>
  );
}
