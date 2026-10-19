import { forwardRef, useEffect, useState, type ButtonHTMLAttributes, type ReactNode, type TextareaHTMLAttributes,
  type InputHTMLAttributes, type SelectHTMLAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { cn, statusTone, humanStatus, type Tone } from "../lib/utils";

type Variant = "default" | "primary" | "danger" | "ghost";

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }>(
  function Button({ variant = "default", busy, className, children, disabled, ...rest }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        disabled={disabled || busy}
        className={cn(
          "inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border px-3.5 text-sm font-semibold transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-60",
          variant === "default" && "border-line-strong bg-panel text-ink hover:bg-sunken",
          variant === "primary" && "border-ink bg-ink text-panel hover:opacity-90",
          variant === "danger" && "border-line-strong bg-panel text-bad hover:bg-bad-soft",
          variant === "ghost" && "border-transparent bg-transparent text-ink-2 hover:bg-sunken",
          className,
        )}
        {...rest}
      >
        {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
        {children}
      </button>
    );
  },
);

/** A button that runs async work, shows a spinner and reports errors as a toast. */
export function ActionButton({ action, success, children, ...rest }: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> & {
  variant?: Variant; action: () => Promise<unknown>; success?: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      {...rest}
      busy={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await action();
          if (success) toast.success(success);
        } catch (error) {
          toast.error((error as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      {children}
    </Button>
  );
}

export function Card({ className, children, ...rest }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLElement>) {
  return <section className={cn("rounded-xl border border-line bg-panel", className)} {...rest}>{children}</section>;
}

const TONES: Record<Tone, string> = {
  neutral: "bg-sunken text-ink-2",
  accent: "bg-accent-soft text-accent-ink",
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  flag: "bg-flag-soft text-flag",
  bad: "bg-bad-soft text-bad",
  violet: "bg-violet-soft text-violet",
};

export function Badge({ tone = "neutral", children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold", TONES[tone], className)}>
      {dot ? <span className="size-1.5 rounded-full bg-current" aria-hidden /> : null}
      {children}
    </span>
  );
}

export function StatusBadge({ status, human }: { status: string; human?: boolean }) {
  return <Badge tone={statusTone(status)} dot>{human ? humanStatus(status) : status.replace(/_/g, " ").toLowerCase()}</Badge>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-[5px] border border-b-2 border-line-strong bg-panel px-1.5 py-px font-mono text-[11px] text-muted">{children}</kbd>;
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[12.5px]", className)}>{children}</span>;
}

export function Field({ label, hint, htmlFor, children, required, error }: {
  label: string; hint?: string; htmlFor: string; children: ReactNode; required?: boolean; error?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-semibold text-ink-2">
        {label}
        {required ? <span className="text-bad" aria-hidden> *</span> : null}
        {hint ? <span className="font-normal text-muted"> — {hint}</span> : null}
      </label>
      {children}
      {error ? <p id={`${htmlFor}-error`} role="alert" className="m-0 text-sm font-medium text-bad">{error}</p> : null}
    </div>
  );
}

const inputBase = "w-full rounded-lg border border-line-strong bg-panel px-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none aria-[invalid=true]:border-bad aria-[invalid=true]:bg-bad-soft/40";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(inputBase, "h-10", props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(inputBase, "min-h-20 py-2 leading-relaxed", props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn(inputBase, "h-10 pr-8", props.className)} />;
}

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="min-w-0 flex-[1_1_420px]">
        {eyebrow ? <div className="mb-2">{eyebrow}</div> : null}
        <h1 className="m-0 text-[28px] font-semibold tracking-tight">{title}</h1>
        {subtitle ? <p className="mt-1.5 mb-0 text-muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line-strong bg-panel px-6 py-10 text-center text-muted">{children}</div>;
}

let mermaidId = 0;

/** A ```mermaid block drawn as a diagram; the library loads only when a page has one. A diagram that does not
 * parse stays as its source. */
function MermaidBlock({ code }: { code: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict",
        theme: document.documentElement.classList.contains("dark") ? "dark" : "default" });
      try {
        const { svg } = await mermaid.render(`mermaid-${++mermaidId}`, code);
        if (live) setSvg(svg);
      } catch {
        if (live) setFailed(true);
      }
    }).catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [code]);
  if (failed || !svg) return <pre><code>{code}</code></pre>;
  return <div className="my-3 overflow-x-auto rounded-lg border border-line bg-panel p-3" dangerouslySetInnerHTML={{ __html: svg }} />;
}

/** The anchor id the controller writes for a heading (project_docs._slug), so a table of contents can link to it. */
function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// Raw HTML is not rendered: the explicit anchors after SRS/SDS headings (for GitHub) would show as text. The heading
// gets the same id from its own text instead.
const ANCHOR = /[ \t]*<a id="[^"]*"><\/a>/g;

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) return textOf((node as { props: { children?: ReactNode } }).props.children);
  return "";
}

function heading(Tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6") {
  return function Heading({ children }: { children?: ReactNode }) {
    return <Tag id={slug(textOf(children))}>{children}</Tag>;
  };
}

const HEADINGS = { h1: heading("h1"), h2: heading("h2"), h3: heading("h3"), h4: heading("h4"), h5: heading("h5"), h6: heading("h6") };

export function Markdown({ source }: { source: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        ...HEADINGS,
        code({ className, children, ...rest }) {
          if (/language-mermaid/.test(className ?? "")) return <MermaidBlock code={String(children).replace(/\n$/, "")} />;
          return <code className={className} {...rest}>{children}</code>;
        },
        a({ href, children }) {
          if (!href?.startsWith("#")) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
          // The control room routes on the URL hash, so an in-document link scrolls inside this document instead.
          return (
            <a href={href} onClick={(e) => {
              e.preventDefault();
              const target = e.currentTarget.closest(".md")?.querySelector(`[id="${CSS.escape(decodeURIComponent(href.slice(1)))}"]`);
              target?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}>{children}</a>
          );
        },
      }}>{source.replace(ANCHOR, "")}</ReactMarkdown>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-5 border-b border-line">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={cn("-mb-px border-b-2 px-0.5 py-2.5 text-sm font-semibold",
            value === tab.id ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink")}
        >
          {tab.label}
          {tab.count !== undefined ? <span className="ml-1.5 text-xs text-muted">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg bg-sunken px-3 py-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 font-semibold">{value}</div>
      {sub ? <div className="text-xs text-muted">{sub}</div> : null}
    </div>
  );
}

export function Loading() {
  return <div className="flex items-center gap-2 py-10 text-muted"><Loader2 className="size-4 animate-spin" aria-hidden />Loading…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <Card className="border-bad/40 p-5">
      <h2 className="m-0 text-base font-semibold text-bad">Something went wrong</h2>
      <p className="mb-0 text-muted">{(error as Error)?.message ?? String(error)}. Check Health or run <Mono>./sdlc doctor</Mono>.</p>
    </Card>
  );
}
