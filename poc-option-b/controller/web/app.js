"use strict";

const $ = (id) => document.getElementById(id);
let token = "";
let selectedId = "";

function elt(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function message(value, ok = false) {
  $("message").textContent = value;
  $("message").className = ok ? "ok" : "";
}

async function api(path, options = {}) {
  if (!token) throw new Error("Nhập controller token trước.");
  const response = await fetch(path, {
    ...options,
    headers: {"X-Controller-Token": token, ...(options.body ? {"Content-Type": "application/json"} : {})},
    cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  return body;
}

async function connect() {
  token = $("token").value;
  try {
    await refresh();
    const features = await api("/api/pilot/features");
    $("feature").replaceChildren(...features.map((feature) => {
      const option = elt("option", feature.id);
      option.value = feature.id;
      return option;
    }));
    $("connection").textContent = "Đã kết nối";
    $("token").value = "";
    message("Đã kết nối controller.", true);
  } catch (error) {
    token = "";
    $("connection").textContent = "Chưa kết nối";
    message(error.message);
  }
}

async function refresh() {
  const runs = await api("/api/runs?limit=100");
  const list = $("runs");
  list.replaceChildren();
  if (!runs.length) list.append(elt("p", "Chưa có run.", "empty"));
  for (const run of runs) {
    const button = elt("button", undefined, `run-item ${selectedId === run.id ? "selected" : ""}`);
    button.append(elt("strong", run.status), elt("span", `${run.id.slice(0, 8)} · ${run.repo_id}`));
    button.onclick = () => selectRun(run.id);
    list.append(button);
  }
  if (selectedId) await selectRun(selectedId, false);
}

function renderPlan(plan, run) {
  const box = elt("div", undefined, "plan");
  box.append(elt("strong", `Plan v${plan.version}`), elt("code", plan.sha256, "hash"));
  for (const task of plan.body.tasks) {
    const item = elt("div", undefined, "task");
    item.append(elt("strong", task.id), elt("p", task.description));
    item.append(elt("small", `Đường dẫn: ${task.allowed_paths.join(", ")} · Phụ thuộc: ${task.dependencies.join(", ") || "không"}`, "muted"));
    const criteria = elt("ul", undefined, "criteria");
    task.acceptance_criteria.forEach((value) => criteria.append(elt("li", value)));
    item.append(criteria);
    box.append(item);
  }
  if (run.status === "WAITING_PLAN_APPROVAL" && plan === run.plans.at(-1)) {
    const actions = elt("div", undefined, "actions");
    actions.append(actionButton("Duyệt plan này", async () => decision("approve-plan", {
      plan_id: plan.id, plan_sha256: plan.sha256, expected_run_version: run.version,
      actor: "web-operator", reason: "Reviewed in local web UI",
    })));
    actions.append(actionButton("Từ chối", async () => decision("reject-plan", {
      plan_id: plan.id, plan_sha256: plan.sha256, expected_run_version: run.version,
      actor: "web-operator", reason: "Rejected in local web UI",
    }), "danger"));
    box.append(actions);
    const revision = elt("details");
    revision.append(elt("summary", "Chỉnh sửa plan JSON"));
    const editor = elt("textarea");
    editor.rows = 14;
    editor.value = JSON.stringify(plan.body, null, 2);
    editor.setAttribute("aria-label", "Plan JSON mới");
    const reason = elt("input");
    reason.placeholder = "Lý do chỉnh sửa";
    reason.setAttribute("aria-label", "Lý do chỉnh sửa");
    revision.append(editor, reason, actionButton("Lưu plan mới", async () => {
      if (!reason.value.trim()) throw new Error("Nhập lý do chỉnh sửa plan.");
      let body;
      try { body = JSON.parse(editor.value); } catch { throw new Error("Plan JSON không hợp lệ."); }
      await decision("revise-plan", {
        expected_run_version: run.version, actor: "web-operator",
        reason: reason.value.trim(), plan: body,
      });
    }));
    box.append(revision);
  }
  return box;
}

function actionButton(label, handler, className = "") {
  const button = elt("button", label, className);
  button.onclick = async () => {
    button.disabled = true;
    try { await handler(); } catch (error) { message(error.message); } finally { button.disabled = false; }
  };
  return button;
}

async function decision(action, payload) {
  await api(`/api/runs/${selectedId}/${action}`, {method: "POST", body: JSON.stringify(payload)});
  message("Quyết định đã được ghi nhận.", true);
  await refresh();
}

async function selectRun(id, refreshList = true) {
  selectedId = id;
  const [run, events] = await Promise.all([api(`/api/runs/${id}`), api(`/api/runs/${id}/events`)]);
  $("run-id").textContent = id;
  const detail = $("detail");
  detail.replaceChildren();
  detail.append(elt("span", run.status, "status"), elt("p", `Repo: ${run.repo_id} · Executor: ${run.executor}`));
  detail.append(elt("code", `Base commit: ${run.base_commit}`, "hash"));
  detail.append(elt("h3", "Yêu cầu"), elt("p", run.requirement));
  detail.append(elt("h3", "Plan"));
  run.plans.forEach((plan) => detail.append(renderPlan(plan, run)));
  detail.append(elt("h3", "Tasks"));
  if (!run.tasks.length) detail.append(elt("p", "Chưa có task được duyệt.", "muted"));
  run.tasks.forEach((task) => detail.append(elt("div", `${task.external_task_id} · ${task.status} · ${task.executor}`, "task")));
  detail.append(elt("h3", "Pull request"));
  if (run.pull_request) {
    const link = elt("a", `PR #${run.pull_request.number}`);
    link.href = run.pull_request.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    detail.append(link, elt("p", `CI: ${run.pull_request.ci_status} · Head: ${run.pull_request.head_sha}`));
    if (run.status === "WAITING_PR_APPROVAL" && run.pull_request.ci_status === "success") {
      detail.append(actionButton("Duyệt PR", () => decision("approve-pr", {
        expected_run_version: run.version, actor: "web-operator", reason: "Reviewed in local web UI",
      })));
    }
  } else detail.append(elt("p", "Chưa có PR.", "muted"));
  detail.append(elt("h3", "Sự kiện"));
  events.slice().reverse().forEach((event) => {
    const row = elt("div", undefined, "event");
    row.append(elt("strong", event.event_type), elt("time", new Date(event.created_at).toLocaleString("vi-VN")));
    detail.append(row);
  });
  if (refreshList) await refresh();
}

$("connect").onclick = () => connect();
$("refresh").onclick = () => refresh().catch((error) => message(error.message));
$("create").onclick = async () => {
  try {
    const run = await api(`/api/pilot/runs/${encodeURIComponent($("feature").value)}`, {method: "POST"});
    selectedId = run.id;
    await refresh();
    message(`Đã tạo run ${run.id}. Dùng CLI để chạy analysis.`, true);
  } catch (error) { message(error.message); }
};
