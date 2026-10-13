"use strict";

// ------------------------------------------------------------------ helpers

const ROLE_LABEL = {
  product_owner: "Product Owner", tech_lead: "Tech Lead", code_owner: "Code Owner", platform_admin: "Platform Admin",
};
const ACTION_LABEL = {
  needs_clarification: "Answer the Business Analyst's questions",
  waiting_spec_approval: "Review spec.md",
  waiting_plan_approval: "Review plan.md and the test plan",
  waiting_pr_approval: "Review the pull request",
  needs_human: "Decide how to continue",
  start: "Start a manual stage",
  classify: "Classify what went wrong",
};
const ACTIVE_WORK = ["RECEIVED", "PREFLIGHT", "SPECIFYING", "PLANNING", "GROUNDING", "READY", "CODING", "REVIEWING", "PR_OPEN"];
let META = { operator: "", failure_categories: [], failure_stages: [], failure_roles: [] };
let timer = null;

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "html") el.innerHTML = value; // only used with escaped markdown output
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(text) {
  return text.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\s)_([^_]+)_(?=\s|$)/g, "$1<em>$2</em>");
}

// Small markdown renderer for artifacts. Input is escaped first, so only these tags can appear.
function markdown(source) {
  const lines = escapeHtml(source || "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^#{1,3} /.test(line)) {
      const level = line.match(/^#+/)[0].length;
      out.push(`<h${level}>${inline(line.slice(level + 1))}</h${level}>`);
      i++;
    } else if (line.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const cells = (row) => row.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const body = rows.filter((row) => !/^\|[\s|:-]+\|$/.test(row));
      const [head, ...rest] = body;
      out.push("<div class='table-wrap'><table><thead><tr>" + cells(head).map((c) => `<th>${c}</th>`).join("") +
        "</tr></thead><tbody>" + rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") +
        "</tbody></table></div>");
    } else if (/^- /.test(line)) {
      const items = [];
      while (i < lines.length && /^- /.test(lines[i])) items.push(`<li>${inline(lines[i++].slice(2))}</li>`);
      out.push(`<ul>${items.join("")}</ul>`);
    } else if (line.trim() === "") {
      i++;
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,3} |- |\|)/.test(lines[i])) para.push(lines[i++]);
      out.push(`<p>${inline(para.join(" "))}</p>`);
    }
  }
  return h("div", { class: "md", html: out.join("\n") });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-SDLC-Client": "web", ...(options.headers || {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || response.statusText));
  }
  return data;
}

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), 5000);
}

async function act(button, work) {
  button.disabled = true;
  try {
    await work();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function pill(status) {
  const cls = { ok: "ok", ready: "ok", COMPLETED: "ok", PASSED: "ok", warn: "warn", plan_only: "warn",
    pending: "warn", fail: "fail", not_ready: "fail", FAILED: "fail", NEEDS_HUMAN: "fail", CANCELLED: "muted",
    DISPUTED: "fail", UNKNOWN: "fail" }[status] || "";
  return h("span", { class: `pill ${cls}` }, status);
}

function when(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}

function render(...nodes) {
  document.getElementById("view").replaceChildren(...nodes.filter((node) => node !== null && node !== undefined && node !== false));
}

function poll(fn, ms) {
  clearTimeout(timer);
  timer = setTimeout(fn, ms);
}

// ------------------------------------------------------------------ inbox

async function inboxView() {
  const items = await api("/api/inbox");
  const groups = {};
  for (const item of items) (groups[item.role] ||= []).push(item);
  const notify = "Notification" in window && Notification.permission === "default"
    ? h("button", { onclick: () => Notification.requestPermission() }, "Enable browser notifications") : null;
  render(
    h("h1", {}, "Inbox"),
    h("p", { class: "sub" }, `You hold every human role. ${items.length} item(s) wait for you.`),
    notify,
    items.length ? null : h("div", { class: "card empty" }, "Nothing waits for you. Start with ", h("a", { href: "#/new" }, "a new request"), "."),
    ...Object.entries(groups).map(([role, list]) => h("section", {},
      h("h2", {}, `As ${ROLE_LABEL[role] || role}`),
      ...list.map((item) => h("div", { class: "card" },
        h("div", { class: "row" },
          h("div", { class: "grow" }, h("strong", {}, item.title), " ", h("span", { class: "muted small" }, item.project)),
          pill(item.status)),
        h("div", {}, ACTION_LABEL[item.action] || item.action, item.stage ? `: ${item.stage}` : ""),
        item.reason ? h("div", { class: "muted small" }, item.reason) : null,
        h("div", { class: "actions" }, h("a", { href: `#/runs/${item.id}` }, "Open →")))))),
  );
  poll(route, 5000);
}

// ------------------------------------------------------------------ new request

async function newRequestView() {
  const projects = (await api("/api/projects")).filter((p) => p.readiness !== "not_ready");
  if (!projects.length) {
    render(h("h1", {}, "New request"),
      h("div", { class: "card empty" }, "No project is ready yet. ", h("a", { href: "#/projects" }, "Add a project"), " first."));
    return;
  }
  const key = crypto.randomUUID();
  const field = (name, label, hint, tag = "textarea") => [
    h("label", { for: name }, label, hint ? h("span", { class: "muted" }, ` — ${hint}`) : null),
    h(tag, { id: name, name })];
  const project = h("select", { id: "project" }, ...projects.map((p) => h("option", { value: p.key }, `${p.key} (${p.readiness})`)));
  const freeText = h("textarea", { id: "free", placeholder: "Describe what you want in your own words…" });
  const draftButton = h("button", {}, "Draft intent.md with AI");
  draftButton.addEventListener("click", () => act(draftButton, async () => {
    const title = document.getElementById("title").value || "Untitled request";
    const draft = await api("/api/intents/draft", { method: "POST", body: { title, text: freeText.value } });
    for (const name of ["title", "problem", "outcome", "users_and_systems", "constraints", "open_questions"]) {
      document.getElementById(name).value = draft[name] || "";
    }
    toast("Draft filled in. Correct it before you submit: submitting accepts intent.md as the Product Owner.");
  }));
  const submit = h("button", { class: "primary" }, "Submit intent.md");
  submit.addEventListener("click", () => act(submit, async () => {
    const intent = {};
    for (const name of ["title", "problem", "outcome", "users_and_systems", "constraints", "open_questions"]) {
      intent[name] = document.getElementById(name).value;
    }
    const run = await api("/api/runs", { method: "POST", body: { project: project.value, intent, idempotency_key: key } });
    location.hash = `#/runs/${run.id}`;
  }));
  render(
    h("h1", {}, "New request"),
    h("p", { class: "sub" }, "You act as Product Owner. The form is intent.md; the Business Analyst turns it into spec.md."),
    h("div", { class: "card" }, h("label", { for: "project" }, "Project"), project),
    h("div", { class: "card" }, h("h3", {}, "Optional: let the Business Analyst draft it"), freeText,
      h("div", { class: "actions" }, draftButton)),
    h("div", { class: "card" },
      ...field("title", "Title", null, "input"),
      ...field("problem", "Problem", "what is wrong or missing today"),
      ...field("outcome", "Desired outcome", "one line per outcome; each becomes an acceptance criterion"),
      ...field("users_and_systems", "Affected users and systems"),
      ...field("constraints", "Constraints"),
      ...field("open_questions", "Open questions", "start a line with [blocking] if work must wait for an answer"),
      h("div", { class: "actions" }, submit)),
  );
}

// ------------------------------------------------------------------ runs

async function runsView() {
  const runs = await api("/api/runs");
  render(
    h("h1", {}, "Runs"),
    h("p", { class: "sub" }, `${runs.length} run(s), newest first.`),
    runs.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Title"), h("th", {}, "Project"), h("th", {}, "Status"),
        h("th", {}, "Waiting for"), h("th", {}, "Updated"))),
      h("tbody", {}, ...runs.map((run) => h("tr", {},
        h("td", {}, h("a", { href: `#/runs/${run.id}` }, run.title)), h("td", {}, run.project), h("td", {}, pill(run.status)),
        h("td", {}, run.waiting ? ROLE_LABEL[run.waiting.role] : ""), h("td", { class: "small muted" }, when(run.updated_at)))))))
      : h("div", { class: "card empty" }, "No runs yet."),
  );
  poll(route, 10000);
}

function latest(run, kind) {
  const list = run.documents.filter((d) => d.kind === kind);
  return list[list.length - 1];
}

function versionPicker(items, render) {
  if (!items.length) return h("p", { class: "muted" }, "Not produced yet.");
  const box = h("div");
  const select = h("select", { style: "max-width: 320px" }, ...items.map((item, index) =>
    h("option", { value: index, selected: index === items.length - 1 },
      `v${item.version} by ${item.author}${item.author_kind === "human" ? " (human)" : ""}`)));
  const show = () => box.replaceChildren(render(items[select.value]));
  select.addEventListener("change", show);
  show();
  return h("div", {}, items.length > 1 ? select : null, box);
}

function documentPanel(document) {
  return h("div", {},
    h("p", { class: "muted small" }, `sha256 ${document.sha256.slice(0, 16)}… · ${document.repo_path}`,
      document.commit_sha ? ` · committed ${document.commit_sha.slice(0, 10)}` : " · not committed"),
    markdown(document.content));
}

function planPanel(plan) {
  return h("div", {},
    h("p", { class: "muted small" }, `sha256 ${plan.sha256.slice(0, 16)}…`, plan.commit_sha ? ` · committed ${plan.commit_sha.slice(0, 10)}` : ""),
    h("p", {}, plan.plan.summary),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Task", "Depends on", "Files", "Criteria", "Validation"].map((t) => h("th", {}, t)))),
      h("tbody", {}, ...plan.plan.tasks.map((task) => h("tr", {},
        h("td", {}, h("strong", {}, task.id), " ", task.title),
        h("td", {}, task.depends_on.join(", ")),
        h("td", {}, ...[...task.modify.map((f) => `modify ${f}`), ...task.create.map((f) => `create ${f}`)].map((t) => h("div", { class: "small" }, h("code", {}, t)))),
        h("td", {}, task.acceptance_criteria.join(", ")),
        h("td", {}, task.validation.join(", "))))))),
    h("h3", {}, `Test plan (${plan.test_plan.cases.length} cases, QA Analyst)`),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Case", "Task", "Criterion", "Test", "File"].map((t) => h("th", {}, t)))),
      h("tbody", {}, ...plan.test_plan.cases.map((c) => h("tr", {},
        h("td", {}, c.id), h("td", {}, c.task_id), h("td", {}, c.criterion),
        h("td", {}, c.description, c.expected_pass ? h("span", { class: "pill warn" }, "expected pass") : null),
        h("td", {}, h("code", {}, c.target_file))))))),
    h("h3", {}, "Grounding check: ", pill(plan.grounding ? plan.grounding.status : "not run")),
    ...(plan.grounding ? plan.grounding.findings : []).map((f) => h("div", { class: "small muted" }, f)),
  );
}

function reasonBox(placeholder) {
  return h("textarea", { placeholder, style: "min-height: 60px" });
}

function decisionPanel(run, reload) {
  const need = run.waiting;
  if (!need) return null;
  const post = (path, body) => api(`/api/runs/${run.id}/${path}`, { method: "POST", body }).then(reload);
  const button = (label, cls, work) => {
    const el = h("button", { class: cls }, label);
    el.addEventListener("click", () => act(el, work));
    return el;
  };
  const card = (title, ...children) => h("div", { class: "card", style: "border-color: var(--accent)" },
    h("h2", { style: "margin-top: 0" }, `${ROLE_LABEL[need.role]}: ${title}`), ...children);

  if (need.action === "start") {
    return card(`start ${need.stage}`, h("p", {}, `The ${need.stage} stage is in manual mode.`),
      h("div", { class: "actions" }, button(`Start ${need.stage}`, "primary", () => post("start-stage", { stage: need.stage }))));
  }
  if (need.action === "needs_clarification") {
    const open = run.clarifications.filter((c) => !c.answer);
    const inputs = open.map((c) => [c, h("textarea", { style: "min-height: 50px" })]);
    return card("answer the questions", ...inputs.map(([c, input]) => h("div", {}, h("label", {}, c.question), input)),
      h("div", { class: "actions" }, button("Send answers", "primary", () =>
        post("answers", { answers: Object.fromEntries(inputs.map(([c, input]) => [c.id, input.value])) }))));
  }
  if (need.action === "waiting_spec_approval") {
    const spec = latest(run, "spec");
    const editor = h("textarea", { class: "code" }, spec.content);
    const reason = reasonBox("Why (required for reject, optional for revise)");
    const edit = h("div", { hidden: true }, h("p", { class: "muted small" }, "Keep acceptance criteria as lines like `- AC-1: …`."), editor);
    return card(`spec.md v${spec.version}`, documentPanel(spec), edit, reason,
      h("div", { class: "actions" },
        button(`Accept spec v${spec.version}`, "primary", () => post("approve-spec", { document_id: spec.id, sha256: spec.sha256 })),
        button("Edit", "", async () => { edit.hidden = !edit.hidden; }),
        button("Save as new version", "", () => post("revise-spec", { content: editor.value, reason: reason.value || null })),
        button("Reject: rewrite", "danger", () => post("reject-spec", { reason: reason.value, next: "redo" })),
        button("Reject: cancel run", "danger", () => post("reject-spec", { reason: reason.value, next: "cancel" }))));
  }
  if (need.action === "waiting_plan_approval") {
    const plan = run.plans[run.plans.length - 1];
    const editor = h("textarea", { class: "code" }, JSON.stringify({ plan: plan.plan, test_plan: plan.test_plan }, null, 2));
    const edit = h("div", { hidden: true }, editor);
    const reason = reasonBox("Why (required for reject, optional for revise)");
    return card(`plan.md v${plan.version}`, planPanel(plan), edit, reason,
      h("div", { class: "actions" },
        button(`Approve plan v${plan.version}`, "primary", () => post("approve-plan", { plan_id: plan.id, sha256: plan.sha256 })),
        button("Edit JSON", "", async () => { edit.hidden = !edit.hidden; }),
        button("Save as new version", "", () => {
          let parsed;
          try { parsed = JSON.parse(editor.value); } catch (error) { throw new Error(`invalid JSON: ${error.message}`); }
          return post("revise-plan", { plan: parsed.plan, test_plan: parsed.test_plan, reason: reason.value || null });
        }),
        button("Reject: replan", "danger", () => post("reject-plan", { reason: reason.value, next: "redo" })),
        button("Reject: cancel run", "danger", () => post("reject-plan", { reason: reason.value, next: "cancel" }))));
  }
  if (need.action === "waiting_pr_approval") {
    const pr = run.pull_request;
    const reason = reasonBox("Requested changes or reason");
    return card("pull request",
      pr.simulated ? h("p", {}, "Simulated pull request (B2): branch ", h("code", {}, run.branch),
        " exists only in the local mirror, at ", h("code", {}, (pr.head_sha || "").slice(0, 10)), ". Real draft PRs arrive in B4.")
        : h("p", {}, h("a", { href: pr.url, target: "_blank", rel: "noopener" }, pr.url)),
      reason,
      h("div", { class: "actions" },
        pr.simulated ? button("Record simulated merge", "primary", () => post("pr-decision", { decision: "approve" })) : null,
        button("Request changes", "", () => post("pr-decision", { decision: "request_changes", reason: reason.value })),
        button("Close without merge", "danger", () => post("pr-decision", { decision: "close", reason: reason.value }))));
  }
  if (need.action === "needs_human") {
    const reason = reasonBox("What you checked and why you decide this (required)");
    return card("the team stopped", h("p", {}, run.reason), reason,
      h("div", { class: "actions" },
        button("Resume", "primary", () => post("resume", { decision: "resume", reason: reason.value })),
        button("Replan", "", () => post("resume", { decision: "replan", reason: reason.value })),
        button("Cancel run", "danger", () => post("resume", { decision: "cancel", reason: reason.value }))));
  }
  if (need.action === "classify") {
    const select = (options) => h("select", {}, ...options.map((o) => h("option", { value: o }, o)));
    const category = select(META.failure_categories), stage = select(META.failure_stages), role = select(META.failure_roles);
    const note = reasonBox("Note (required for OTHER); secondary causes go here");
    return card("classify the root cause",
      h("p", { class: "muted" }, "Required for failed runs and for runs a human had to correct."),
      h("label", {}, "Category"), category, h("label", {}, "Stage"), stage, h("label", {}, "Role whose output was wrong"), role,
      h("label", {}, "Note"), note,
      h("div", { class: "actions" }, button("Save classification", "primary", () =>
        post("classify-failure", { category: category.value, stage: stage.value, role: role.value, note: note.value }))));
  }
  return null;
}

async function runView(id) {
  const [run, events] = await Promise.all([api(`/api/runs/${id}`), api(`/api/runs/${id}/events`)]);
  const reload = () => runView(id);
  const tabs = {
    Thread: () => h("div", {}, ...["intent", "spec", "plan", "review", "report"].map((kind) => h("div", { class: "card" },
      h("h3", { style: "margin-top: 0" }, `${kind}.md`),
      kind === "plan" ? versionPicker(run.plans, planPanel) : versionPicker(run.documents.filter((d) => d.kind === kind), documentPanel)))),
    Tasks: () => run.tasks.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Task", "Status", "Attempts", "Depends on"].map((t) => h("th", {}, t)))),
      h("tbody", {}, ...run.tasks.map((t) => h("tr", {}, h("td", {}, h("strong", {}, t.id), " ", t.title),
        h("td", {}, pill(t.status)), h("td", {}, t.attempts), h("td", {}, t.depends_on.join(", ")))))))
      : h("p", { class: "muted" }, "Tasks appear after plan approval."),
    Work: () => h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ...["Job", "Role", "Engine", "Status", "Attempts", "Error"].map((t) => h("th", {}, t)))),
      h("tbody", {}, ...run.executions.map((e) => h("tr", {}, h("td", {}, e.kind), h("td", {}, e.role), h("td", {}, e.engine || ""),
        h("td", {}, pill(e.status)), h("td", {}, e.attempts), h("td", { class: "small" }, e.error || "")))))),
    Decisions: () => run.approvals.length ? h("ul", { class: "timeline" }, ...run.approvals.map((a) => h("li", {},
      h("strong", {}, `${a.stage} ${a.decision}`), ` by ${a.actor} as ${ROLE_LABEL[a.role] || a.role}`,
      a.reason ? h("div", { class: "muted" }, a.reason) : null, h("div", { class: "muted" }, when(a.created_at)))))
      : h("p", { class: "muted" }, "No human decisions yet."),
    Events: () => h("ul", { class: "timeline" }, ...events.slice().reverse().map((e) => h("li", {},
      h("strong", {}, e.type), ` · ${e.actor}${e.role ? ` (${e.role})` : ""} · `, h("span", { class: "muted" }, when(e.created_at)),
      Object.keys(e.payload).length ? h("div", { class: "muted" }, JSON.stringify(e.payload)) : null))),
  };
  const body = h("div");
  const tabBar = h("div", { class: "tabs" });
  const select = (name) => {
    sessionStorage.setItem("runTab", name);
    tabBar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.textContent === name));
    body.replaceChildren(tabs[name]());
  };
  for (const name of Object.keys(tabs)) tabBar.append(h("button", { onclick: () => select(name) }, name));
  const cancel = !["COMPLETED", "CANCELLED", "FAILED", "CLOSED_UNMERGED"].includes(run.status)
    ? h("button", { class: "danger", onclick: (event) => act(event.target, async () => {
      const reason = prompt("Why cancel this run?");
      if (reason !== null) { await api(`/api/runs/${run.id}/cancel`, { method: "POST", body: { reason } }); reload(); }
    }) }, "Cancel run") : null;
  render(
    h("p", { class: "small" }, h("a", { href: "#/runs" }, "← Runs")),
    h("div", { class: "row" }, h("h1", { class: "grow" }, run.title), pill(run.status)),
    h("p", { class: "sub" }, `${run.project} · branch `, h("code", {}, run.branch || "not yet"),
      run.base_commit ? [" · base ", h("code", {}, run.base_commit.slice(0, 10))] : null,
      ` · modes ${Object.entries(run.stage_modes).map(([k, v]) => `${k}:${v}`).join(" ")}`),
    run.reason && !run.waiting ? h("p", { class: "muted" }, run.reason) : null,
    decisionPanel(run, reload),
    tabBar, body,
    h("div", { class: "actions" }, cancel),
  );
  const saved = sessionStorage.getItem("runTab");
  select(tabs[saved] ? saved : "Thread");
  poll(reload, ACTIVE_WORK.includes(run.status) && !run.waiting ? 2000 : 10000);
}

// ------------------------------------------------------------------ projects

async function projectsView() {
  const projects = await api("/api/projects");
  const key = h("input", { placeholder: "task-board" });
  const url = h("input", { placeholder: "https://github.com/owner/repo, git@github.com:owner/repo or /absolute/path" });
  const add = h("button", { class: "primary" }, "Add project");
  add.addEventListener("click", () => act(add, async () => {
    await api("/api/projects", { method: "POST", body: { key: key.value.trim(), repo_url: url.value.trim() } });
    toast("Project added and checked.");
    projectsView();
  }));
  render(
    h("h1", {}, "Projects"),
    h("p", { class: "sub" }, "As Platform Admin. A project needs AGENTS.md, CLAUDE.md and .sdlc/project.yaml; see examples/task-board."),
    ...projects.map((p) => {
      const check = h("button", {}, "Check again");
      check.addEventListener("click", () => act(check, async () => { await api(`/api/projects/${p.key}/check`, { method: "POST" }); projectsView(); }));
      return h("div", { class: "card" },
        h("div", { class: "row" }, h("strong", { class: "grow" }, p.key), pill(p.readiness)),
        h("div", { class: "muted small" }, p.repo_url, ` · ${p.default_branch}`, p.checked_commit ? ` at ${p.checked_commit.slice(0, 10)}` : ""),
        h("div", { class: "table-wrap", style: "margin-top: 10px" }, h("table", {}, h("tbody", {},
          ...p.report.map((item) => h("tr", {}, h("td", { style: "width: 90px" }, pill(item.status)),
            h("td", { style: "width: 190px" }, item.name), h("td", {}, item.detail)))))),
        h("div", { class: "actions" }, check));
    }),
    h("div", { class: "card" }, h("h2", { style: "margin-top: 0" }, "Add a project"),
      h("label", {}, "Key ", h("span", { class: "muted" }, "— must equal `project:` in .sdlc/project.yaml")), key,
      h("label", {}, "Repository"), url, h("div", { class: "actions" }, add)),
  );
}

// ------------------------------------------------------------------ health

async function healthView() {
  const data = await fetch("/health/ready").then((r) => r.json());
  render(
    h("h1", {}, "Health"),
    h("p", { class: "sub" }, `Readiness: ${data.status}. Run ./sdlc doctor for the full report.`),
    h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, ...data.checks.map((c) => h("tr", {},
      h("td", { style: "width: 80px" }, pill(c.status)), h("td", { style: "width: 120px" }, c.name),
      h("td", {}, c.detail, c.hint && c.status !== "ok" ? h("div", { class: "muted small" }, c.hint) : null)))))),
  );
  poll(route, 10000);
}

// ------------------------------------------------------------------ shell

let lastCount = null;
async function refreshInboxCount() {
  try {
    const items = await api("/api/inbox");
    const badge = document.getElementById("inbox-count");
    badge.hidden = items.length === 0;
    badge.textContent = items.length;
    document.title = items.length ? `(${items.length}) SDLC Control Room` : "SDLC Control Room";
    if (lastCount !== null && items.length > lastCount && "Notification" in window && Notification.permission === "granted") {
      const item = items[items.length - 1];
      new Notification("The team is waiting for you", { body: `${item.title}: ${ACTION_LABEL[item.action] || item.action}` });
    }
    lastCount = items.length;
  } catch (error) { /* the health page explains outages */ }
}

async function route() {
  const [, page, id] = (location.hash || "#/inbox").split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (page || "inbox")));
  try {
    if (page === "new") await newRequestView();
    else if (page === "runs" && id) await runView(id);
    else if (page === "runs") await runsView();
    else if (page === "projects") await projectsView();
    else if (page === "health") await healthView();
    else await inboxView();
  } catch (error) {
    render(h("h1", {}, "Something went wrong"), h("p", {}, error.message),
      h("p", { class: "muted" }, "Check ", h("a", { href: "#/health" }, "Health"), " or run ./sdlc doctor."));
  }
}

window.addEventListener("hashchange", () => { clearTimeout(timer); route(); });
api("/api/meta").then((meta) => {
  META = meta;
  document.getElementById("who").textContent = `${meta.operator} · team: ${meta.team_mode}`;
}).catch(() => {});
setInterval(refreshInboxCount, 5000);
refreshInboxCount();
route();
