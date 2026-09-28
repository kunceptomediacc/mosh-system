import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const modules = process.env.MOSH_NODE_MODULES;
if (!modules) throw new Error("MOSH_NODE_MODULES must point to the bundled Node modules directory");
const require = createRequire(path.join(modules, "package.json"));
const { chromium } = require("playwright");
const pixelmatchModule = require("pixelmatch");
const pixelmatch = pixelmatchModule.default || pixelmatchModule;
const { PNG } = require("pngjs");

const testRoot = path.dirname(fileURLToPath(import.meta.url));
const baselineRoot = path.join(testRoot, "baselines");
const artifactRoot = path.join(testRoot, "artifacts");
const updateBaselines = process.env.MOSH_UPDATE_BASELINES === "1";
const viewports = [
  { name: "mobile", width: 375, height: 900 },
  { name: "tablet", width: 768, height: 900 },
  { name: "desktop", width: 1440, height: 1000 },
];

const baseUrl = process.env.MOSH_DASHBOARD_URL || "http://127.0.0.1:8765/";
const tokenFile = process.env.MOSH_TOKEN_FILE;
if (!tokenFile) throw new Error("MOSH_TOKEN_FILE is required");
const token = (await readFile(tokenFile, "utf8")).trim();
const edge = process.env.MOSH_BROWSER || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const browser = await chromium.launch({ headless: true, executablePath: edge });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const consoleErrors = [];
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });

async function checkVisualBaselines() {
  await mkdir(baselineRoot, { recursive: true });
  await mkdir(artifactRoot, { recursive: true });
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    const actual = await page.screenshot({ fullPage: true, animations: "disabled" });
    const baselinePath = path.join(baselineRoot, `locked-${viewport.name}.png`);
    if (updateBaselines) {
      await writeFile(baselinePath, actual);
      continue;
    }
    let expected;
    try { expected = await readFile(baselinePath); }
    catch { throw new Error(`missing visual baseline: ${baselinePath}; run with MOSH_UPDATE_BASELINES=1`); }
    const expectedPng = PNG.sync.read(expected);
    const actualPng = PNG.sync.read(actual);
    if (expectedPng.width !== actualPng.width || expectedPng.height !== actualPng.height) {
      throw new Error(`visual dimensions changed for ${viewport.name}: ${expectedPng.width}x${expectedPng.height} -> ${actualPng.width}x${actualPng.height}`);
    }
    const diff = new PNG({ width: actualPng.width, height: actualPng.height });
    const changed = pixelmatch(expectedPng.data, actualPng.data, diff.data, actualPng.width, actualPng.height, { threshold: 0.1 });
    const ratio = changed / (actualPng.width * actualPng.height);
    if (ratio > 0.005) {
      await writeFile(path.join(artifactRoot, `locked-${viewport.name}-actual.png`), actual);
      await writeFile(path.join(artifactRoot, `locked-${viewport.name}-diff.png`), PNG.sync.write(diff));
      throw new Error(`visual regression for ${viewport.name}: ${(ratio * 100).toFixed(2)}% pixels changed`);
    }
  }
}

async function checkAccessibility() {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  const audit = await page.evaluate(() => {
    const interactive = [...document.querySelectorAll("a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])")];
    const unnamed = interactive.filter((node) => !(node.getAttribute("aria-label") || node.getAttribute("aria-labelledby") || node.labels?.length || node.textContent.trim() || node.getAttribute("title"))).map((node) => node.outerHTML);
    const undersized = interactive.filter((node) => { const box = node.getBoundingClientRect(); return box.width > 0 && box.height > 0 && (box.width < 24 || box.height < 24); }).map((node) => ({ html: node.outerHTML, width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height }));
    return { unnamed, undersized, main: document.querySelectorAll("main").length, h1: document.querySelectorAll("h1").length };
  });
  if (audit.unnamed.length) throw new Error(`unnamed controls: ${JSON.stringify(audit.unnamed)}`);
  if (audit.undersized.length) throw new Error(`controls below 24px target: ${JSON.stringify(audit.undersized)}`);
  if (audit.main !== 1 || audit.h1 !== 1) throw new Error(`landmark/heading audit failed: ${JSON.stringify(audit)}`);
  await page.keyboard.press("Tab");
  if ((await page.locator(":focus").textContent())?.trim() !== "Skip to main content") throw new Error("skip link is not the first keyboard target");
  await page.keyboard.press("Enter");
  if ((await page.locator(":focus").getAttribute("id")) !== "main-content") throw new Error("skip link does not focus main content");
  const session = await page.context().newCDPSession(page);
  const tree = await session.send("Accessibility.getFullAXTree");
  const badControls = tree.nodes.filter((node) => ["button", "textbox", "link"].includes(node.role?.value) && !node.name?.value);
  if (badControls.length) throw new Error(`accessibility tree has ${badControls.length} unnamed controls`);
}

function fixtureResponse(url, empty = false) {
  const parsed = new URL(url);
  const pathName = parsed.pathname;
  const tasks = empty ? [] : Array.from({ length: 9 }, (_, index) => ({
    task_id: `TASK-${String(index + 1).padStart(3, "0")}`,
    objective: `Seeded objective ${index + 1}`,
    status: "queued",
    provider: "codex",
    account_id: "codex-primary",
    risk: "low",
  }));
  if (pathName === "/api/v1/health") return { data: { status: "healthy" } };
  if (pathName === "/api/v1/summary") return { data: { tasks: tasks.length, runs: empty ? 0 : 1, events: empty ? 0 : 2, approvals: 0, agents: empty ? 0 : 1 } };
  if (pathName === "/api/v1/system-status") return { data: { status: empty ? "limited" : "operational", components: [{ name: "durable_storage", status: "ready", ready: 1, total: 1 }, { name: "agents", status: empty ? "unavailable" : "ready", ready: empty ? 0 : 1, total: empty ? 0 : 1 }, { name: "accounts", status: empty ? "unavailable" : "ready", ready: empty ? 0 : 2, total: empty ? 0 : 2 }, { name: "routing_resilience", status: empty ? "degraded" : "ready", ready: empty ? 0 : 2, total: 2 }], work: { active_tasks: empty ? 0 : 9, attention_tasks: 0 }, gates: { pending_approvals: 0, pending_side_effects: 0 } } };
  if (pathName === "/api/v1/cleanup-plans") return { data: [] };
  if (pathName === "/api/v1/agents") return { data: empty ? [] : [{ name: "Codex", adapter: "codex", capabilities: ["health"], status: "ready" }] };
  if (pathName === "/api/v1/accounts") return { data: empty ? [] : [{ alias: "primary", provider: "codex", account_id: "codex-primary", status: "ready" }] };
  if (pathName === "/api/v1/projects") return empty
    ? { data: [], meta: { total: 0, limit: 50, offset: 0 } }
    : { data: [{ project_id: "MOSH-CORE", total_tasks: 9, active_tasks: 7, review_tasks: 1, completed_tasks: 1, attention_tasks: 0, last_updated: "2026-09-23T00:02:00Z" }], meta: { total: 1, limit: 50, offset: 0 } };
  if (pathName === "/api/v1/workspace") return { data: { workspace: "Moshpit", git: { state: "metadata_parked", branch: null, commit: null, staged: 0, unstaged: 0, untracked: 0 }, areas: [{ name: "apps", present: true, file_count: 12, directory_count: 4 }, { name: "bridge", present: true, file_count: 8, directory_count: 3 }, { name: "docs", present: true, file_count: 20, directory_count: 5 }, { name: "scripts", present: true, file_count: 6, directory_count: 2 }] } };
  if (pathName === "/api/v1/integrations") return { data: { agents: { registered: empty ? 0 : 1, ready: empty ? 0 : 1 }, capabilities: empty ? [] : [{ name: "health", agent_count: 1 }], areas: [{ kind: "plugins_installed", present: true, entry_count: empty ? 0 : 2 }, { kind: "plugins_registry", present: false, entry_count: 0 }, { kind: "tools_mcp", present: true, entry_count: empty ? 0 : 1 }, { kind: "tools_local", present: false, entry_count: 0 }] } };
  if (pathName === "/api/v1/plugins") return { data: empty ? [] : [{ plugin_id: "github", name: "GitHub", provider: "github", status: "unconfigured", auth_type: "github_app", health_status: "unknown", capabilities: ["repository.read"], requested_scopes: ["metadata:read"], granted_scopes: [], allowed_agents: [], approval_required_for_writes: true, last_checked_at: null }] };
  if (pathName === "/api/v1/plugin-activity") return { data: { allowlisted_repositories: empty ? 0 : 1, reads: { authorized: empty ? 0 : 2, completed: empty ? 0 : 1, failed: 0, pending: empty ? 0 : 1 }, last_activity_at: empty ? null : "2026-09-23T00:03:00Z" } };
  if (pathName === "/api/v1/governance-status") return { data: { profiles: { experiment: empty ? 0 : 1, business: 0 }, classifications: { internal: empty ? 0 : 1, confidential: 0, restricted: 0 }, statuses: { draft: 0, active: empty ? 0 : 1, blocked: 0 }, requirements: { gap: empty ? 0 : 2, evidenced: empty ? 0 : 1, waived: 0 }, changes: { pending: empty ? 0 : 1, approved: empty ? 0 : 2, rejected: 0 }, artifacts: { pending: empty ? 0 : 1, accepted: empty ? 0 : 1, rejected: 0 }, compliance_claims: "prohibited" } };
  if (pathName === "/api/v1/workflow-status") return { data: { statuses: { candidate: empty ? 0 : 1, approved: empty ? 0 : 2, rejected: 0, retired: 0 }, sources: { local: empty ? 0 : 1, skill: empty ? 0 : 1, plugin: 0, mcp: 0, n8n: empty ? 0 : 1 }, plans: { ready: empty ? 0 : 1, approval_required: empty ? 0 : 1 }, tool_statuses: { observed: empty ? 0 : 1, approved: empty ? 0 : 1, rejected: 0 }, tool_effects: { none: 0, read: empty ? 0 : 1, write: empty ? 0 : 1, destructive: 0 }, external_effect_patterns: empty ? 0 : 1, execution: "disabled" } };
  if (pathName === "/api/v1/video-status") return { data: { projects: { draft: empty ? 0 : 1, in_production: empty ? 0 : 1, review: 0, approved: 0, cancelled: 0 }, stages: { pending: empty ? 0 : 8, completed: empty ? 0 : 1 }, publishing: "approval_gated_unavailable" } };
  if (pathName === "/api/v1/identity-status") return { data: { providers: { local_dev: empty ? 0 : 1, google_oidc: empty ? 0 : 1, oidc: 0 }, active_principals: empty ? 0 : 2, roles: { owner: empty ? 0 : 1, admin: 0, developer: empty ? 0 : 1, operator: 0, client: 0, viewer: 0 }, local_identity: "development_only" } };
  if (pathName === "/api/v1/memory-stores") return { data: { status: "healthy", schema_version: 14, stores: [{ name: "task_context", record_count: empty ? 0 : 9, last_recorded_at: empty ? null : "2026-09-23T00:02:00Z" }, { name: "execution_history", record_count: empty ? 0 : 1, last_recorded_at: empty ? null : "2026-09-23T00:01:00Z" }, { name: "audit_events", record_count: empty ? 0 : 2, last_recorded_at: empty ? null : "2026-09-23T00:02:00Z" }] } };
  if (pathName === "/api/v1/learning-status") return { data: { memory: { candidate: empty ? 0 : 2, validated: empty ? 0 : 1, approved: 0, rejected: 0, expired: 0 }, skills: { candidate: empty ? 0 : 1, validated: 0, approved: 0, rejected: 0, retired: 0 }, pending_approvals: { memory: empty ? 0 : 1, skills: 0 }, task_bindings: { total: empty ? 0 : 2, usable: empty ? 0 : 1 }, embeddings: { status: "disabled", indexed_records: 0 } } };
  if (pathName === "/api/v1/log-streams") return { data: { status: "available", total_events: empty ? 0 : 12, streams: [{ name: "task", event_count: empty ? 0 : 7, last_event_at: empty ? null : "2026-09-23T00:02:00Z" }, { name: "run", event_count: empty ? 0 : 5, last_event_at: empty ? null : "2026-09-23T00:01:00Z" }, { name: "approval", event_count: 0, last_event_at: null }, { name: "side_effect", event_count: 0, last_event_at: null }, { name: "cleanup", event_count: 0, last_event_at: null }, { name: "other", event_count: 0, last_event_at: null }], signals: { failures: empty ? 0 : 1, cancellations: 0, recoveries: 0 } } };
  if (pathName === "/api/v1/approvals") return { data: [], meta: { total: 0 } };
  if (pathName === "/api/v1/side-effects") return { data: [], meta: { total: 0 } };
  if (pathName === "/api/v1/tasks") {
    const limit = Number(parsed.searchParams.get("limit") || 8);
    const offset = Number(parsed.searchParams.get("offset") || 0);
    return { data: tasks.slice(offset, offset + limit), meta: { total: tasks.length, limit, offset } };
  }
  const eventMatch = pathName.match(/^\/api\/v1\/tasks\/([^/]+)\/events$/);
  if (eventMatch) {
    if (empty) return { data: [], meta: { has_next: false, next_cursor: 0 } };
    const cursor = Number(parsed.searchParams.get("after_event_id") || 0);
    return cursor
      ? { data: [{ event_id: 11, event_type: "task.updated", created_at: "2026-09-23T00:01:00Z", from_status: "queued", to_status: "running", payload: {} }], meta: { has_next: false, next_cursor: 11 } }
      : { data: [{ event_id: 10, event_type: "task.created", created_at: "2026-09-23T00:00:00Z", from_status: null, to_status: "queued", payload: {} }], meta: { has_next: true, next_cursor: 10 } };
  }
  const taskMatch = pathName.match(/^\/api\/v1\/tasks\/([^/]+)$/);
  if (taskMatch) {
    const task = tasks.find((item) => item.task_id === decodeURIComponent(taskMatch[1])) || tasks[0];
    return { data: { task, runs: [], approvals: [], side_effects: [] } };
  }
  throw new Error(`unhandled fixture route: ${parsed.pathname}${parsed.search}`);
}

async function newFixturePage(empty = false) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const submissions = [];
  await context.route("**/api/v1/**", async (route) => {
    if (route.request().method() === "POST") {
      submissions.push({ body: route.request().postDataJSON(), writeToken: route.request().headers()["x-mosh-write-token"] });
      const task = { task_id: "TASK-UI-SEEDED", objective: submissions.at(-1).body.objective, status: "queued", approval_required: true };
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ data: { task, run: { status: "queued" } } }) });
      return;
    }
    const body = fixtureResponse(route.request().url(), empty);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  const fixturePage = await context.newPage();
  await fixturePage.goto(baseUrl, { waitUntil: "networkidle" });
  await fixturePage.getByLabel("Session bearer token").fill("seeded-token-value");
  await fixturePage.getByRole("button", { name: "Unlock" }).click();
  await fixturePage.getByText("Connected. Token remains in this tab only.").waitFor();
  return { context, fixturePage, submissions };
}

async function checkSeededPagination() {
  const { context, fixturePage, submissions } = await newFixturePage(false);
  try {
    if ((await fixturePage.locator(":focus").getAttribute("id")) !== "dashboard") throw new Error("successful authentication did not move focus to the operational dashboard");
    await fixturePage.keyboard.press("Tab");
    if ((await fixturePage.locator(":focus").getAttribute("id")) !== "throw-objective") throw new Error("keyboard focus did not enter Throw It In at the objective field");
    await fixturePage.getByLabel("What should MOSH work on?").fill("Create a reviewable seeded plan");
    await fixturePage.getByLabel("Account").selectOption("codex-primary");
    await fixturePage.getByLabel("Risk").selectOption("medium");
    await fixturePage.getByLabel("Submission token").fill("seeded-write-token");
    await fixturePage.getByRole("button", { name: "Queue for approval" }).click();
    await fixturePage.getByText("TASK-UI-SEEDED is queued and waiting for owner approval.").waitFor();
    if (submissions.length !== 1 || submissions[0].writeToken !== "seeded-write-token") throw new Error("Throw It In did not send the separate write token exactly once");
    if (JSON.stringify(submissions[0].body) !== JSON.stringify({ objective: "Create a reviewable seeded plan", account_id: "codex-primary", risk: "medium" })) throw new Error(`unexpected task submission: ${JSON.stringify(submissions[0].body)}`);
    if ((await fixturePage.getByLabel("Submission token").inputValue()) !== "") throw new Error("write token was not cleared after submission");
    await fixturePage.locator("#tasks-page").getByText("1 / 2", { exact: true }).waitFor();
    await fixturePage.getByText("operational", { exact: true }).waitFor();
    await fixturePage.getByText("routing resilience").waitFor();
    await fixturePage.getByText("MOSH-CORE").waitFor();
    await fixturePage.getByText("9 tasks").waitFor();
    await fixturePage.getByText("metadata parked").waitFor();
    await fixturePage.getByText("12 files · 4 folders").waitFor();
    await fixturePage.getByText("2 entries").waitFor();
    await fixturePage.getByText("health · 1").waitFor();
    await fixturePage.getByText("unconfigured · health unknown · 0 scopes granted").waitFor();
    await fixturePage.getByText("1 / 2 completed").waitFor();
    await fixturePage.getByText("compliance claims prohibited").waitFor();
    await fixturePage.getByText("healthy · schema v14").waitFor();
    await fixturePage.getByText("9", { exact: true }).last().waitFor();
    await fixturePage.getByText("embeddings disabled · 1 / 2 bindings usable").waitFor();
    await fixturePage.getByText("available · 12 events").waitFor();
    if ((await fixturePage.locator("#tasks .task").count()) !== 8) throw new Error("first task page did not contain eight seeded records");
    await fixturePage.getByRole("button", { name: "Next task page" }).focus();
    await fixturePage.keyboard.press("Enter");
    await fixturePage.locator("#tasks-page").getByText("2 / 2", { exact: true }).waitFor();
    await fixturePage.getByText("TASK-009").waitFor();
    if (!(await fixturePage.getByRole("button", { name: "Next task page" }).isDisabled())) throw new Error("next task page control should be disabled on the final page");
    await fixturePage.getByText("TASK-009").click();
    await fixturePage.getByText("task.created").waitFor();
    const session = await context.newCDPSession(fixturePage);
    const tree = await session.send("Accessibility.getFullAXTree");
    const nodes = tree.nodes.map((node) => ({ role: node.role?.value, name: node.name?.value })).filter((node) => node.role && node.name);
    const taskButtons = nodes.filter((node) => node.role === "button" && node.name.startsWith("TASK-"));
    if (!taskButtons.length || taskButtons.some((node) => node.name.length > 180)) throw new Error(`task announcements are missing or too verbose: ${JSON.stringify(taskButtons)}`);
    for (const required of [
      { role: "region", name: "MOSH operational dashboard" },
      { role: "heading", name: "Durable tasks" },
      { role: "button", name: "Previous task page" },
      { role: "button", name: "Next task page" },
      { role: "button", name: "Load earlier history" },
    ]) if (!nodes.some((node) => node.role === required.role && node.name === required.name)) throw new Error(`accessibility tree is missing ${required.role} '${required.name}'`);
    await fixturePage.getByRole("button", { name: "Load earlier history" }).click();
    await fixturePage.getByText("task.updated").waitFor();
    if ((await fixturePage.locator("#events .event").count()) !== 2) throw new Error("event pagination did not append the second seeded event");
  } finally {
    await context.close();
  }
}

async function checkSeededEmptyStates() {
  const { context, fixturePage } = await newFixturePage(true);
  try {
    for (const message of [
      "No tasks in this range.",
      "No projects have durable tasks yet.",
      "No agents or accounts registered.",
      "No approvals recorded.",
      "No side-effect requests recorded.",
      "No cleanup plans are pending.",
    ]) await fixturePage.getByText(message).waitFor();
    const values = await fixturePage.locator(".metric b").allTextContents();
    if (values.length !== 5 || values.some((value) => value !== "0")) throw new Error(`empty summary metrics were not all zero: ${values.join(",")}`);
  } finally {
    await context.close();
  }
}

try {
  await checkVisualBaselines();
  await checkAccessibility();
  await checkSeededPagination();
  await checkSeededEmptyStates();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  if (!(await page.getByRole("heading", { name: /Everything moving/ }).isVisible())) throw new Error("dashboard hero is not visible");
  await page.getByLabel("Session bearer token").fill("invalid-token-value");
  await page.getByRole("button", { name: "Unlock" }).click();
  await page.getByText("A valid bearer token is required").waitFor();
  await page.waitForTimeout(250); // Let the deliberately unauthorized parallel reads settle.
  consoleErrors.length = 0; // Expected 401s from the deliberate invalid-auth probe.
  await page.getByLabel("Session bearer token").fill(token);
  await page.getByRole("button", { name: "Unlock" }).click();
  await page.getByText("Connected. Token remains in this tab only.").waitFor();
  if ((await page.locator(".metric").count()) !== 5) throw new Error("expected five summary metrics");
  if ((await page.locator("#roster .roster-item").count()) < 1) throw new Error("expected roster entries");
  if ((await page.locator("#tasks .task").count()) > 0) {
    await page.locator("#tasks .task").first().click();
    await page.locator("#task-detail .facts").waitFor();
  }
  if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
  console.log(JSON.stringify({ ok: true, visual_baselines: viewports.map(({ name }) => name), accessibility: true, keyboard_focus_order: true, accessibility_tree: true, throw_it_in: true, system_status: true, projects: true, workspace_files_git: true, plugins_tools: true, memory: true, learning_status: true, logs: true, separate_write_token: true, seeded_task_pagination: true, seeded_event_pagination: true, seeded_empty_states: true, hero: true, invalid_auth: true, authenticated: true, roster: true, task_detail: true }));
} finally {
  await browser.close();
}
