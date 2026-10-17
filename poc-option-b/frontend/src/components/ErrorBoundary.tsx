import { Component, type ErrorInfo, type ReactNode } from "react";

/** Send a crash of the control room to the controller's log, so it can be read without a screenshot. */
export function reportClientError(message: string, stack: string, where: string): void {
  try {
    void fetch("/api/client-errors", {
      method: "POST", headers: { "Content-Type": "application/json", "X-SDLC-Client": "web" },
      body: JSON.stringify({ message: message.slice(0, 2000), stack: stack.slice(0, 8000), where, url: location.href }),
    });
  } catch { /* nothing to do: the page shows the error */ }
}

/** A rendering error shows its message and a reload button instead of a blank page; going to another page resets it. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportClientError(error.message, `${error.stack ?? ""}\n${info.componentStack ?? ""}`, "render");
  }

  componentDidUpdate(previous: { resetKey: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-6 flex flex-col gap-3 rounded-xl border border-bad/40 bg-bad-soft p-5 text-sm">
        <b className="text-base">This page hit an error</b>
        <span>It was sent to the controller's log (.data/log/api.log). Reload to continue; nothing you saved is lost.</span>
        <pre className="m-0 max-h-60 overflow-auto rounded-md bg-panel p-3 font-mono text-xs whitespace-pre-wrap">{this.state.error.message}</pre>
        <div><button type="button" className="rounded-lg bg-ink px-3 py-1.5 font-semibold text-panel" onClick={() => location.reload()}>Reload</button></div>
      </div>
    );
  }
}
