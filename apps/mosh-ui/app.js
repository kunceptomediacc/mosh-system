"use strict";

if ("serviceWorker" in navigator && location.protocol !== "file:") {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

const state = { token: sessionStorage.getItem("mosh.token") || "", taskOffset: 0, taskLimit: 8, total: 0, selected: null, eventCursor: 0 };
const $ = (selector) => document.querySelector(selector);

if (["127.0.0.1", "localhost"].includes(location.hostname)) {
  $("#google-sign-in").href = "http://127.0.0.1:1455/";
}

async function api(path, options = {}) {
  const headers = { ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}), ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body?.error?.message || `Request failed (${response.status})`);
    error.code = body?.error?.code || "request_failed";
    throw error;
  }
  return body;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function text(tag, value, className) { const node = document.createElement(tag); node.textContent = String(value); if (className) node.className = className; return node; }
function empty(node, message = "No records yet.") { clear(node); const item = text("div", message, "empty-state"); node.append(item); }

function lockDashboard(message = null) {
  $("#dashboard").hidden = true;
  $("#signed-in-identity").hidden = true;
  $("#logout").hidden = true;
  $("#health-dot").classList.remove("online");
  $("#health-label").textContent = "Locked";
  if (message) {
    $("#auth-message").textContent = message;
    $("#auth-message").classList.add("error");
  }
}

function renderSummary(data) {
  const target = $("#summary"); clear(target);
  const fields = [["tasks", "Tasks"], ["runs", "Runs"], ["events", "Events"], ["approvals", "Approvals"], ["agents", "Agents"]];
  fields.forEach(([key, label]) => { const card = document.createElement("div"); card.className = "metric"; card.append(text("b", data[key] ?? 0), text("span", label)); target.append(card); });
}

function renderSystemStatus(data) {
  const target = $("#system-status"); clear(target); $("#system-health").textContent = data.status;
  const components = document.createElement("div"); components.className = "status-components";
  data.components.forEach((component) => {
    const item = document.createElement("div"); item.className = `status-component ${component.status}`;
    item.append(text("strong", component.name.replaceAll("_", " ")), text("b", `${component.ready} / ${component.total}`), text("span", component.status)); components.append(item);
  });
  const signals = document.createElement("div"); signals.className = "status-signals";
  [["Active work", data.work.active_tasks], ["Needs attention", data.work.attention_tasks], ["Pending approvals", data.gates.pending_approvals], ["Pending side effects", data.gates.pending_side_effects]].forEach(([label, value]) => {
    const item = document.createElement("div"); item.className = "status-signal"; item.append(text("span", label), text("b", value)); signals.append(item);
  });
  target.append(components, signals);
}

function renderProjects(data, meta) {
  const target = $("#projects"); clear(target); $("#project-count").textContent = `${meta.total} total`;
  if (!data.length) return empty(target, "No projects have durable tasks yet.");
  data.forEach((project) => {
    const item = document.createElement("article"); item.className = "project-card";
    const stats = document.createElement("div"); stats.className = "project-stats";
    [["tasks", project.total_tasks], ["active", project.active_tasks], ["review", project.review_tasks], ["done", project.completed_tasks], ["attention", project.attention_tasks]].forEach(([label, value]) => stats.append(text("span", `${value} ${label}`)));
    const updated = document.createElement("time"); updated.dateTime = project.last_updated; updated.textContent = `Updated ${project.last_updated}`;
    item.append(text("strong", project.project_id), stats, updated); target.append(item);
  });
}

function renderWorkspace(data) {
  const target = $("#workspace"); clear(target); const git = data.git;
  $("#git-state").textContent = git.branch ? `${git.state} · ${git.branch}` : git.state.replaceAll("_", " ");
  data.areas.forEach((area) => {
    const item = document.createElement("div"); item.className = `workspace-area${area.present ? "" : " missing"}`;
    item.append(text("strong", area.name), text("span", area.present ? `${area.file_count} files · ${area.directory_count} folders` : "not present")); target.append(item);
  });
}

function renderIntegrations(data, plugins, activity) {
  const target = $("#integrations"); clear(target);
  $("#integration-agents").textContent = `${data.agents.ready} / ${data.agents.registered} agents ready`;
  const areas = document.createElement("div"); areas.className = "integration-areas";
  data.areas.forEach((area) => {
    const item = document.createElement("div"); item.className = `integration-area${area.present ? "" : " missing"}`;
    item.append(text("strong", area.kind.replaceAll("_", " ")), text("span", area.present ? `${area.entry_count} entries` : "not present")); areas.append(item);
  });
  const capabilities = document.createElement("div"); capabilities.className = "capability-list";
  capabilities.append(text("strong", "Registered capabilities", "capability-title"));
  if (!data.capabilities.length) capabilities.append(text("span", "No capabilities registered.", "empty-inline"));
  data.capabilities.forEach((item) => capabilities.append(text("span", `${item.name} · ${item.agent_count}`, "capability-chip")));
  const registry = document.createElement("div"); registry.className = "plugin-registry";
  registry.append(text("strong", "Governed plugin registry", "capability-title"));
  if (!plugins.length) registry.append(text("span", "No plugin definitions registered.", "empty-inline"));
  plugins.forEach((plugin) => {
    const item = document.createElement("div"); item.className = "plugin-entry";
    item.append(text("b", plugin.name), text("span", `${plugin.status} · health ${plugin.health_status} · ${plugin.granted_scopes.length} scopes granted`));
    registry.append(item);
  });
  const reads = document.createElement("div"); reads.className = "plugin-activity";
  reads.append(
    text("strong", "Governed GitHub reads", "capability-title"),
    text("b", `${activity.reads.completed} / ${activity.reads.authorized} completed`),
    text("span", `${activity.allowlisted_repositories} repositories allowlisted · ${activity.reads.pending} pending · ${activity.reads.failed} failed`),
  );
  registry.append(reads);
  target.append(areas, capabilities, registry);
}

function renderMemory(data) {
  const target = $("#memory"); clear(target);
  $("#memory-health").textContent = `${data.status} · schema v${data.schema_version}`;
  data.stores.forEach((store) => {
    const item = document.createElement("div"); item.className = "memory-store";
    const recorded = store.last_recorded_at ? `Latest ${store.last_recorded_at}` : "No records yet";
    item.append(text("strong", store.name.replaceAll("_", " ")), text("b", store.record_count), text("span", recorded)); target.append(item);
  });
}

function renderLearning(data) {
  const target = $("#learning-status"); clear(target);
  const bindings = data.task_bindings || { usable: 0, total: 0 };
  $("#learning-embedding").textContent = `embeddings ${data.embeddings.status} · ${bindings.usable} / ${bindings.total} bindings usable`;
  [["Memory", data.memory, data.pending_approvals.memory], ["Skills", data.skills, data.pending_approvals.skills]].forEach(([label, states, pending]) => {
    const group = document.createElement("section"); group.className = "learning-group";
    group.append(text("strong", label, "learning-title"), text("span", `${pending} approvals pending`, "learning-pending"));
    const grid = document.createElement("div"); grid.className = "learning-states";
    Object.entries(states).forEach(([name, value]) => { const item = document.createElement("div"); item.append(text("b", value), text("span", name)); grid.append(item); });
    group.append(grid); target.append(group);
  });
}

function renderGovernance(data) {
  const target = $("#governance-status"); clear(target);
  $("#governance-claim").textContent = `compliance claims ${data.compliance_claims}`;
  [["Profiles", data.profiles], ["Classification", data.classifications], ["Status", data.statuses], ["Requirements", data.requirements], ["Changes", data.changes], ["Artifacts", data.artifacts]].forEach(([label, values]) => {
    const group = document.createElement("section"); group.className = "governance-group";
    group.append(text("strong", label, "learning-title"));
    const grid = document.createElement("div"); grid.className = "governance-states";
    Object.entries(values).forEach(([name, value]) => { const item = document.createElement("div"); item.append(text("b", value), text("span", name)); grid.append(item); });
    group.append(grid); target.append(group);
  });
}

function renderWorkflows(data) {
  const target = $("#workflow-status"); clear(target);
  $("#workflow-execution").textContent = `execution ${data.execution} · ${data.external_effect_patterns} externally gated`;
  [["Lifecycle", data.statuses], ["Sources", data.sources], ["Plans", data.plans], ["Tool review", data.tool_statuses], ["Tool effects", data.tool_effects]].forEach(([label, values]) => {
    const group = document.createElement("section"); group.className = "governance-group";
    group.append(text("strong", label, "learning-title"));
    const grid = document.createElement("div"); grid.className = "governance-states";
    Object.entries(values).forEach(([name, value]) => { const item = document.createElement("div"); item.append(text("b", value), text("span", name)); grid.append(item); });
    group.append(grid); target.append(group);
  });
}

function renderVideo(data) {
  const target = $("#video-status"); clear(target);
  $("#video-publishing").textContent = `publishing ${data.publishing.replaceAll("_", " ")}`;
  [["Projects", data.projects], ["Stages", data.stages]].forEach(([label, values]) => {
    const group = document.createElement("section"); group.className = "governance-group";
    group.append(text("strong", label, "learning-title"));
    const grid = document.createElement("div"); grid.className = "governance-states";
    Object.entries(values).forEach(([name, value]) => { const item = document.createElement("div"); item.append(text("b", value), text("span", name)); grid.append(item); });
    group.append(grid); target.append(group);
  });
}

function renderIdentity(data) {
  const target = $("#identity-status"); clear(target);
  $("#identity-local").textContent = `local identity ${data.local_identity.replaceAll("_", " ")}`;
  [["Providers", data.providers], ["Roles", data.roles]].forEach(([label, values]) => {
    const group = document.createElement("section"); group.className = "governance-group";
    group.append(text("strong", label, "learning-title"));
    const grid = document.createElement("div"); grid.className = "governance-states";
    Object.entries(values).forEach(([name, value]) => { const item = document.createElement("div"); item.append(text("b", value), text("span", name)); grid.append(item); });
    group.append(grid); target.append(group);
  });
  target.append(text("p", `${data.active_principals} active principals`, "empty-inline"));
}

function renderLogs(data) {
  const target = $("#logs"); clear(target);
  $("#logs-health").textContent = `${data.status} · ${data.total_events} events`;
  const streams = document.createElement("div"); streams.className = "log-streams";
  data.streams.forEach((stream) => {
    const item = document.createElement("div"); item.className = "log-stream";
    item.append(text("strong", stream.name.replaceAll("_", " ")), text("b", stream.event_count), text("span", stream.last_event_at ? `Latest ${stream.last_event_at}` : "No events yet")); streams.append(item);
  });
  const signals = document.createElement("div"); signals.className = "log-signals";
  signals.append(text("strong", "Lifecycle signals", "log-signals-title"));
  [["Failures", data.signals.failures], ["Cancellations", data.signals.cancellations], ["Recoveries", data.signals.recoveries]].forEach(([label, value]) => {
    const item = document.createElement("div"); item.className = "log-signal"; item.append(text("span", label), text("b", value)); signals.append(item);
  });
  target.append(streams, signals);
}

function renderTasks(data, meta) {
  state.total = meta.total; const target = $("#tasks"); clear(target);
  if (!data.length) return empty(target, "No tasks in this range.");
  data.forEach((task) => {
    const button = document.createElement("button"); button.type = "button"; button.className = `task${state.selected === task.task_id ? " active" : ""}`;
    const objective = String(task.objective || "No objective").replace(/\s+/g, " ").trim();
    const objectiveSummary = objective.length > 120 ? `${objective.slice(0, 117)}…` : objective;
    button.setAttribute("aria-label", `${task.task_id}. ${objectiveSummary}. Status ${task.status}.`);
    const copy = document.createElement("div"); copy.append(text("strong", task.task_id), text("p", task.objective));
    button.append(copy, text("span", task.status, "badge")); button.addEventListener("click", () => selectTask(task.task_id)); target.append(button);
  });
  const page = Math.floor(state.taskOffset / state.taskLimit) + 1;
  $("#tasks-page").textContent = `${page} / ${Math.max(1, Math.ceil(meta.total / state.taskLimit))}`;
  $("#tasks-prev").disabled = state.taskOffset === 0; $("#tasks-next").disabled = state.taskOffset + state.taskLimit >= meta.total;
}

function renderDetail(snapshot) {
  const target = $("#task-detail"); clear(target); const task = snapshot.task;
  $("#detail-title").textContent = task.task_id;
  const facts = document.createElement("div"); facts.className = "facts";
  [["Status", task.status], ["Provider", task.provider || "unassigned"], ["Account", task.account_id || "unassigned"], ["Risk", task.risk], ["Runs", snapshot.runs.length], ["Approvals", snapshot.approvals.length]].forEach(([label, value]) => {
    const fact = document.createElement("div"); fact.className = "fact"; fact.append(text("span", label), text("strong", value)); facts.append(fact);
  });
  target.append(facts);
}

function appendEvents(data, meta, reset) {
  const target = $("#events"); if (reset) clear(target);
  if (!data.length && reset) empty(target, "No events recorded.");
  data.forEach((event) => { const item = document.createElement("div"); item.className = "event"; item.append(text("strong", event.event_type), text("small", event.created_at)); const note = event.payload?.reason || `${event.from_status || "start"} → ${event.to_status || "recorded"}`; item.append(text("p", note)); target.append(item); });
  state.eventCursor = meta.next_cursor || state.eventCursor; $("#events-more").hidden = !meta.has_next;
}

function renderCleanup(data) {
  const target = $("#cleanup"); clear(target); if (!data.length) return empty(target, "No cleanup plans are pending.");
  data.slice(0, 6).forEach((plan) => { const item = document.createElement("article"); item.className = "cleanup"; item.append(text("strong", plan.plan_id), text("p", `${plan.status} · retain until ${plan.retain_until || "unspecified"}`)); target.append(item); });
}

function renderRoster(agents, accounts) {
  const target = $("#roster"); clear(target);
  const records = [
    ...agents.map((item) => ({ id: item.name, detail: `${item.adapter} · ${item.capabilities.join(", ") || "no capabilities"}`, status: item.status, kind: "agent" })),
    ...accounts.map((item) => ({ id: item.alias, detail: `${item.provider} · ${item.account_id}`, status: item.status, kind: "account" })),
  ];
  if (!records.length) return empty(target, "No agents or accounts registered.");
  records.forEach((record) => { const item = document.createElement("div"); item.className = "roster-item"; const copy = document.createElement("div"); copy.append(text("span", record.kind, "kind"), text("strong", record.id), text("p", record.detail)); const status = text("span", "", `status-dot ${record.status}`); status.setAttribute("aria-label", record.status); status.setAttribute("role", "img"); item.append(copy, status); target.append(item); });
}

function renderSubmissionAccounts(accounts) {
  const select = $("#throw-account"); clear(select);
  accounts.filter((account) => account.status === "ready").forEach((account) => {
    const option = document.createElement("option"); option.value = account.account_id; option.textContent = `${account.alias} · ${account.provider}`; select.append(option);
  });
  $("#throw-form button[type='submit']").disabled = select.options.length === 0;
}

function renderApprovals(data, meta) {
  const target = $("#approvals"); clear(target); $("#approval-count").textContent = `${meta.total} total`;
  if (!data.length) return empty(target, "No approvals recorded.");
  data.forEach((approval) => { const item = document.createElement("div"); item.className = "queue-item"; const copy = document.createElement("div"); copy.append(text("strong", approval.task_id), text("p", `${approval.scope} requested by ${approval.requested_by}`), text("span", approval.created_at, "queue-meta")); const status = text("span", "", `status-dot ${approval.decision}`); status.setAttribute("aria-label", approval.decision); status.setAttribute("role", "img"); item.append(copy, status); target.append(item); });
}

function renderSideEffects(data, meta) {
  const target = $("#side-effects"); clear(target); $("#effect-count").textContent = `${meta.total} total`;
  if (!data.length) return empty(target, "No side-effect requests recorded.");
  data.forEach((request) => { const item = document.createElement("div"); item.className = "queue-item"; const copy = document.createElement("div"); copy.append(text("strong", request.action), text("p", `${request.task_id} · ${request.risk} risk`), text("span", `expires ${request.expires_at}`, "queue-meta")); const status = text("span", "", `status-dot ${request.status}`); status.setAttribute("aria-label", request.status); status.setAttribute("role", "img"); item.append(copy, status); target.append(item); });
}

async function loadTasks() { const body = await api(`/api/v1/tasks?limit=${state.taskLimit}&offset=${state.taskOffset}`); renderTasks(body.data, body.meta); }
async function selectTask(taskId) {
  state.selected = taskId; state.eventCursor = 0; await loadTasks();
  const [detail, events] = await Promise.all([api(`/api/v1/tasks/${encodeURIComponent(taskId)}`), api(`/api/v1/tasks/${encodeURIComponent(taskId)}/events?limit=8`)]);
  renderDetail(detail.data); appendEvents(events.data, events.meta, true);
}

async function unlock(token) {
  state.token = token; const [health, me, summary, systemStatus, cleanup, agents, accounts, projects, workspace, integrations, plugins, pluginActivity, memory, learning, governance, workflows, video, identity, logs, approvals, effects] = await Promise.all([
    api("/api/v1/health"), api("/api/v1/me"), api("/api/v1/summary"), api("/api/v1/system-status"), api("/api/v1/cleanup-plans"), api("/api/v1/agents"), api("/api/v1/accounts"),
    api("/api/v1/projects?limit=50"), api("/api/v1/workspace"), api("/api/v1/integrations"), api("/api/v1/plugins"), api("/api/v1/plugin-activity"), api("/api/v1/memory-stores"), api("/api/v1/learning-status"), api("/api/v1/governance-status"), api("/api/v1/workflow-status"), api("/api/v1/video-status"), api("/api/v1/identity-status"), api("/api/v1/log-streams"), api("/api/v1/approvals?limit=20"), api("/api/v1/side-effects?limit=20"),
  ]);
  if (token) sessionStorage.setItem("mosh.token", token); $("#dashboard").hidden = false; $("#health-dot").classList.add("online"); $("#health-label").textContent = health.data.status;
  $("#signed-in-identity").hidden = false; $("#signed-in-identity").textContent = `${me.data.display_alias} · ${me.data.roles.join(", ")}`;
  renderSummary(summary.data); renderSystemStatus(systemStatus.data); renderCleanup(cleanup.data); renderRoster(agents.data, accounts.data); renderSubmissionAccounts(accounts.data); renderProjects(projects.data, projects.meta); renderWorkspace(workspace.data); renderIntegrations(integrations.data, plugins.data, pluginActivity.data); renderMemory(memory.data); renderLearning(learning.data); renderGovernance(governance.data); renderWorkflows(workflows.data); renderVideo(video.data); renderIdentity(identity.data); renderLogs(logs.data); renderApprovals(approvals.data, approvals.meta); renderSideEffects(effects.data, effects.meta);
  await loadTasks(); $("#auth-message").textContent = state.token ? "Connected. Token remains in this tab only." : "Connected with a secure Google session."; $("#auth-message").classList.remove("error"); $("#logout").hidden = Boolean(state.token);
}

$("#auth-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await unlock($("#token").value.trim()); $("#token").value = ""; $("#dashboard").focus(); } catch (error) { $("#auth-message").textContent = error.message; $("#auth-message").classList.add("error"); } });
$("#logout").addEventListener("click", async () => { await api("/api/v1/logout", { method: "POST" }); sessionStorage.removeItem("mosh.token"); location.reload(); });
$("#throw-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = event.currentTarget; const submit = form.querySelector("button[type='submit']"); const message = $("#throw-message");
  submit.disabled = true; message.classList.remove("error"); message.textContent = "Queueing task…";
  try {
    const result = await api("/api/v1/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MOSH-Write-Token": $("#throw-token").value.trim() },
      body: JSON.stringify({ objective: $("#throw-objective").value.trim(), account_id: $("#throw-account").value, risk: $("#throw-risk").value }),
    });
    $("#throw-token").value = ""; $("#throw-objective").value = "";
    message.textContent = `${result.data.task.task_id} is queued and waiting for owner approval.`;
    state.taskOffset = 0; await loadTasks();
  } catch (error) { message.textContent = error.message; message.classList.add("error"); }
  finally { submit.disabled = $("#throw-account").options.length === 0; }
});
$("#tasks-prev").addEventListener("click", async () => { state.taskOffset = Math.max(0, state.taskOffset - state.taskLimit); await loadTasks(); });
$("#tasks-next").addEventListener("click", async () => { state.taskOffset += state.taskLimit; await loadTasks(); });
$("#events-more").addEventListener("click", async () => { const body = await api(`/api/v1/tasks/${encodeURIComponent(state.selected)}/events?limit=8&after_event_id=${state.eventCursor}`); appendEvents(body.data, body.meta, false); });
unlock(state.token).catch((error) => {
  sessionStorage.removeItem("mosh.token"); state.token = "";
  lockDashboard(error.code === "session_expired" ? error.message : null);
});
