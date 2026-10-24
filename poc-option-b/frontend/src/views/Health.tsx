import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import { Card, ErrorBox, Loading, Mono, PageHeader, StatusBadge } from "../components/ui";
import type { Check } from "../lib/api";

export function HealthView() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => fetch("/health/ready").then((r) => r.json() as Promise<{ status: string; checks: Check[] }>),
    refetchInterval: 10_000,
  });
  if (health.isLoading) return <Loading />;
  if (health.error || !health.data) return <ErrorBox error={health.error} />;
  return (
    <>
      <PageHeader title={<span className="inline-flex items-center gap-3">Health<StatusBadge status={health.data.status} /></span>}
        subtitle={<>Fast readiness checks, every 10 seconds. Run <Mono>./sdlc doctor</Mono> for the full report (GX10 capacity, CLI logins).</>} />
      <Card className="p-2">
        <ul className="m-0 list-none p-0">
          {health.data.checks.map((c) => {
            const Icon = c.status === "ok" ? CheckCircle2 : c.status === "warn" ? CircleAlert : XCircle;
            const color = c.status === "ok" ? "text-good" : c.status === "warn" ? "text-warn" : "text-bad";
            return (
              <li key={c.name} className="flex gap-3 rounded-lg px-3 py-2.5">
                <Icon className={`mt-0.5 size-4 shrink-0 ${color}`} aria-label={c.status} />
                <span className="w-40 shrink-0 font-semibold">{c.name}</span>
                <span className="min-w-0 text-muted">{c.detail}{c.hint && c.status !== "ok" ? <span className="block text-ink-2">{c.hint}</span> : null}</span>
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}
