import type { ReactNode } from "react";
import { Link } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bell } from "lucide-react";
import { useInbox } from "../components/Shell";
import { Badge, Button, Card, Empty, ErrorBox, Loading, Mono, PageHeader } from "../components/ui";
import { api, type InboxItem, type RunDetail } from "../lib/api";
import { ACTION_LABEL, ago, parseCriteria, role, statusTone, type Tone } from "../lib/utils";

const ROLE_TONE: Record<string, Tone> = { code_owner: "accent", tech_lead: "warn", product_owner: "violet", platform_admin: "neutral",
  solution_architect: "flag" };
const ROLE_ORDER = ["code_owner", "tech_lead", "solution_architect", "product_owner", "platform_admin"];

function Highlights({ id }: { id: string }) {
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api<RunDetail>(`/api/runs/${id}`), staleTime: 5000 });
  const r = run.data;
  if (!r) return null;
  const plan = r.plans[r.plans.length - 1];
  const spec = [...r.documents].reverse().find((d) => d.kind === "spec");
  const criteria = parseCriteria(spec?.content);
  const pr = r.pull_request;
  const passed = r.tasks.filter((t) => t.status === "PASSED").length;
  const covered = plan ? criteria.filter((c) => plan.test_plan.cases.some((x) => x.criterion === c.id)).length : 0;
  const pills: ReactNode[] = [];
  if (r.waiting?.action === "waiting_pr_approval" && pr) {
    pills.push(<Badge key="ci" tone={statusTone(pr.ci_status)}>CI {pr.ci_status}</Badge>);
    const review = [...r.documents].reverse().find((d) => d.kind === "review");
    const verdict = review?.content.match(/Verdict: \*\*(\w+)\*\*/)?.[1];
    if (verdict) pills.push(<Badge key="rv" tone={verdict === "pass" ? "good" : "warn"}>Review {verdict}</Badge>);
    if (r.tasks.length) pills.push(<Badge key="t" tone="neutral">{passed}/{r.tasks.length} tasks passed</Badge>);
    if (criteria.length) pills.push(<Badge key="ac" tone="neutral">{covered}/{criteria.length} AC with tests</Badge>);
  }
  if (r.waiting?.action === "waiting_plan_approval" && plan) {
    const g = plan.grounding;
    pills.push(<Badge key="g" tone={g?.status === "pass" ? "good" : "bad"}>Grounding {g?.status ?? "not run"}</Badge>);
    pills.push(<Badge key="w" tone={g?.warnings?.length ? "warn" : "neutral"}>{g?.warnings?.length ?? 0} warning(s)</Badge>);
    const guards = plan.test_plan.cases.filter((c) => c.expected_pass).length;
    pills.push(<Badge key="ep" tone={guards ? "flag" : "neutral"}>{guards} expected pass</Badge>);
    pills.push(<Badge key="tc" tone="neutral">{plan.plan.tasks.length} tasks · {plan.test_plan.cases.length} cases</Badge>);
  }
  if (r.waiting?.action === "waiting_spec_approval" && spec) {
    pills.push(<Badge key="ac" tone="neutral">{criteria.length} criteria</Badge>);
    const open = spec.content.split("## Open questions")[1]?.split("\n").filter((l) => l.startsWith("- ")).length ?? 0;
    pills.push(<Badge key="q" tone="neutral">{open} open question(s)</Badge>);
  }
  if (!pills.length) return null;
  return <div className="flex flex-wrap gap-2">{pills}</div>;
}

function ItemCard({ item }: { item: InboxItem }) {
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-center gap-2.5">
        <Badge tone={ROLE_TONE[item.role] ?? "neutral"}>{item.role_name ?? role(item.role)} · {ACTION_LABEL[item.action] ?? item.action}{item.questions ? ` (${item.questions})` : ""}</Badge>
        <Mono className="text-muted">{item.project} · {item.id.slice(0, 8)} · {ago(item.updated_at)}</Mono>
      </div>
      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="min-w-0 flex-[1_1_360px]">
          <h2 className="m-0 text-xl font-semibold tracking-tight">{item.title}</h2>
          {item.questions ? <p className="mt-1.5 mb-0 text-muted">{item.questions} open question(s) for {item.role_name}: open the run's Questions tab.</p>
            : item.reason ? <p className="mt-1.5 mb-0 truncate text-muted">{item.reason}</p> : null}
          {item.stage ? <p className="mt-1.5 mb-0 text-muted">Stage: {item.stage}</p> : null}
        </div>
        <Highlights id={item.id} />
      </div>
      <div>
        <Link href={`/runs/${item.id}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-ink px-3.5 font-semibold text-panel hover:opacity-90 hover:text-panel">
          Open and decide <ArrowRight className="size-4" aria-hidden />
        </Link>
      </div>
    </Card>
  );
}

export function InboxView() {
  const inbox = useInbox();
  if (inbox.isLoading) return <Loading />;
  if (inbox.error) return <ErrorBox error={inbox.error} />;
  const items = inbox.data ?? [];
  const byRole = ROLE_ORDER.map((r) => [r, items.filter((i) => i.role === r)] as const).filter(([, list]) => list.length);
  const canNotify = "Notification" in window && Notification.permission === "default";
  return (
    <>
      <PageHeader
        title="Inbox"
        subtitle={items.length ? `${items.length} item(s) wait for you. Runs move on their own between the gates.` : "Nothing waits for you."}
        actions={
          <>
            {byRole.map(([r, list]) => <Badge key={r} tone="neutral" className="border border-line bg-panel">{role(r)} · {list.length}</Badge>)}
            {canNotify ? (
              <Button variant="ghost" onClick={() => Notification.requestPermission()}><Bell className="size-4" aria-hidden />Notify me</Button>
            ) : null}
          </>
        }
      />
      {items.length === 0 ? (
        <Empty>The team is working or idle. Start with <Link href="/new">a new request</Link>.</Empty>
      ) : (
        byRole.map(([r, list]) => (
          <section key={r} className="flex flex-col gap-3" aria-label={`As ${role(r)}`}>
            <h2 className="m-0 text-sm font-semibold uppercase tracking-wide text-muted">As {role(r)}</h2>
            {list.map((item) => <ItemCard key={item.id} item={item} />)}
          </section>
        ))
      )}
    </>
  );
}
