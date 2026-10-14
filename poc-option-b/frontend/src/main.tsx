import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Router, Switch } from "wouter";
import { useHashLocation } from "wouter/use-hash-location";
import { Toaster } from "sonner";
import "./index.css";
import { Shell } from "./components/Shell";
import { InboxView } from "./views/Inbox";
import { NewRequestView } from "./views/NewRequest";
import { RunsView } from "./views/Runs";
import { RunView } from "./views/Run";
import { ProjectsView } from "./views/Projects";
import { EvalsView, EvalView } from "./views/Evals";
import { ReportsView } from "./views/Reports";
import { HealthView } from "./views/Health";

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <Router hook={useHashLocation}>
        <Shell>
          <Switch>
            <Route path="/new"><NewRequestView /></Route>
            <Route path="/runs/:id">{(p) => <RunView key={p.id} id={p.id} />}</Route>
            <Route path="/runs"><RunsView /></Route>
            <Route path="/projects"><ProjectsView /></Route>
            <Route path="/evals/:id">{(p) => <EvalView key={p.id} id={p.id} />}</Route>
            <Route path="/evals"><EvalsView /></Route>
            <Route path="/reports"><ReportsView /></Route>
            <Route path="/health"><HealthView /></Route>
            <Route><InboxView /></Route>
          </Switch>
        </Shell>
      </Router>
      <Toaster position="bottom-right" richColors closeButton />
    </QueryClientProvider>
  </StrictMode>,
);
