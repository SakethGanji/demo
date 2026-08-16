// Shared harness for the UI regression suite.
//
// These tests drive a REAL browser against a REAL API. Every assertion is
// computed from the DOM or from a re-read of the API — never from a toast, and
// never hardcoded. A hardcoded pass is worse than a failing test.
//
// Requires the API and the UI dev server to be running (see tests/README.md).

import { chromium } from "playwright";

export const BASE = process.env.UI_BASE || "http://localhost:5173";
export const API = process.env.API_BASE || "http://localhost:8001/api/v1";
export const ADMIN = process.env.ADMIN_USER_ID || "00000000-0000-0000-0000-000000000001";
export const JSON_HEADERS = { "X-User-Id": ADMIN, "Content-Type": "application/json" };

export async function open({ width = 1440, height = 1000 } = {}) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`[console.error] ${m.text()}`); });
  page.on("response", (r) => { if (r.status() >= 500) errors.push(`[HTTP ${r.status()}] ${r.url()}`); });
  return { browser, page, errors };
}

export const go = async (page, url) => {
  await page.goto(BASE + url, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
};
export const tab = async (page, label) => {
  await page.locator("button.tab", { hasText: label }).first().click();
  await page.waitForTimeout(900);
};
export const btn = async (page, text) => {
  await page.locator("button", { hasText: text }).first().click();
  await page.waitForTimeout(800);
};

/** Create a dataset from inline rows. Returns its id. */
export async function seed(rows) {
  const fd = new FormData();
  fd.append("data", JSON.stringify(rows));
  const res = await fetch(`${API}/upload?sync=true`, {
    method: "POST", headers: { "X-User-Id": ADMIN }, body: fd,
  });
  if (!res.ok) throw new Error(`seed failed: ${res.status} ${(await res.text()).slice(0, 200)}`);
  return (await res.json()).dataset_id;
}

export async function apiGet(path) {
  const res = await fetch(`${API}${path}`, { headers: { "X-User-Id": ADMIN } });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}
export async function apiPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "POST", headers: JSON_HEADERS, body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, body: res.status === 204 ? null : await res.json().catch(() => null) };
}
export async function apiDelete(path) {
  const res = await fetch(`${API}${path}`, { method: "DELETE", headers: { "X-User-Id": ADMIN } });
  return res.status;
}

export const DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001";

/** A viewer seat in the Default team, created if the install doesn't have one.
 *
 *  Two tests used to just *assume* a viewer existed — which was true only
 *  because earlier exploratory work had left one behind. On a freshly seeded
 *  database they failed in setup. A suite that depends on ambient state isn't
 *  a suite; it creates what it needs.
 */
export async function ensureViewer() {
  const members = await apiGet(`/teams/${DEFAULT_TEAM}/members`);
  const existing = (members.items || []).find((m) => m.role === "viewer");
  if (existing) return existing;

  const email = `uitest-viewer-${Date.now().toString(36)}@example.com`;
  const created = await fetch(`${API}/auth/users`, {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ email, name: "UI test viewer", team_id: DEFAULT_TEAM }),
  });
  if (!created.ok) throw new Error(`could not create a viewer user: ${created.status} ${await created.text()}`);
  const user = await created.json();

  // The create may already place them in the team; make the role explicit.
  await fetch(`${API}/teams/${DEFAULT_TEAM}/members`, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ email, role: "viewer" }),
  });
  await fetch(`${API}/teams/${DEFAULT_TEAM}/members/${user.id}`, {
    method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ role: "viewer" }),
  });
  const after = await apiGet(`/teams/${DEFAULT_TEAM}/members`);
  const seat = (after.items || []).find((m) => m.user_id === user.id);
  if (!seat) throw new Error("viewer user created but not a team member");
  return seat;
}

/** Delete datasets created by a test, so the suite leaves nothing behind. */
export async function cleanup(datasetIds) {
  for (const id of datasetIds.filter(Boolean)) {
    try { await apiDelete(`/datasets/${id}`); } catch { /* best effort */ }
  }
}

/** Minimal test registry: `test(name, fn)` then `runAll()`. */
const tests = [];
export function test(name, fn) { tests.push({ name, fn }); }

export async function runAll(suiteName) {
  let passed = 0;
  const failures = [];
  console.log(`\n${suiteName}`);
  for (const t of tests) {
    const started = Date.now();
    try {
      await t.fn();
      passed++;
      console.log(`  PASS  ${t.name}  (${Date.now() - started}ms)`);
    } catch (e) {
      failures.push({ name: t.name, error: e });
      console.log(`  FAIL  ${t.name}  — ${e.message.split("\n")[0]}`);
    }
  }
  console.log(`\n  ${passed}/${tests.length} passed`);
  if (failures.length) {
    console.log("\nFAILURES:");
    for (const f of failures) console.log(`\n  ${f.name}\n  ${f.error.stack || f.error.message}`);
  }
  return failures.length;
}

/** Assertion helpers that report the actual value on failure. */
export function assert(cond, msg) { if (!cond) throw new Error(msg); }
export function assertEqual(actual, expected, what) {
  if (actual !== expected) throw new Error(`${what}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}
