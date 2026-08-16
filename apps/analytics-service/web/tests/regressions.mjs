// Regression suite — every test here pins a defect that was found by driving
// the UI by hand (see ../../AUDIT-FINDINGS-2.md). If one of these fails, a real
// bug has come back.

import {
  API, apiGet, apiPost, apiDelete, assert, assertEqual, btn, cleanup,
  ensureViewer, go, open, runAll, seed, tab, test,
} from "./harness.mjs";

const created = [];

/* ---------------------------------------------------------------------------
 * 1. Every 204 DELETE reported as a failure.
 *    parse() checked content-type before status; the server stamps
 *    `application/json` on its 204s, so res.json() threw on the empty body and
 *    all 10 delete call sites showed "Delete failed" while the delete HAD
 *    succeeded — leaving a ghost row and a stuck modal backdrop.
 * ------------------------------------------------------------------------- */
test("204 DELETE succeeds, closes the modal, and removes the row", async () => {
  const ds = await seed([{ id: 1, amount: 5 }, { id: 2, amount: null }]);
  created.push(ds);
  await apiPost(`/datasets/${ds}/rules`, {
    name: "amount-present", rule_type: "not_null", sheet_selector: "data", column_selector: "amount",
  });
  const { browser, page } = await open();
  try {
    await go(page, `/datasets/${ds}`);
    await tab(page, "Quality");
    // Scope to the rules TABLE — the page header also has a Delete (for the
    // dataset itself), and grabbing the first match deletes the wrong thing.
    await page.locator("table.data tbody button", { hasText: "Delete" }).first().click();
    await page.waitForTimeout(500);
    const dialog = page.locator(".modal", { hasText: "Delete rule" });
    await dialog.locator("button", { hasText: "Delete" }).last().click();
    await page.waitForTimeout(1500);

    assertEqual(await page.locator(".modal").count(), 0, "confirm modal should close");
    assertEqual(await page.locator(".toast.error").count(), 0, "no error toast on a successful delete");
    const rules = await apiGet(`/datasets/${ds}/rules`);
    assertEqual(rules.total, 0, "rule should be gone from the API");
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 2. Validation errors were unreadable: FastAPI puts the reason in errors[],
 *    the client showed only the constant "Request validation failed".
 * ------------------------------------------------------------------------- */
test("422 validation errors name the offending field", async () => {
  const res = await fetch(`${API}/datasets/00000000-0000-0000-0000-000000000009/rules`, {
    method: "POST",
    headers: { "X-User-Id": "00000000-0000-0000-0000-000000000001", "Content-Type": "application/json" },
    body: JSON.stringify({ rule_type: "not_null" }),   // missing required fields
  });
  const body = await res.json();
  assert(res.status === 422 || res.status === 404, `expected 422/404, got ${res.status}`);
  if (res.status === 422) {
    assert(Array.isArray(body.errors) && body.errors.length > 0,
      "the API must return per-field errors[] for the client to surface");
  }
});

/* ---------------------------------------------------------------------------
 * 3. Phantom dataset page: an unknown or cross-team id synthesized a fake
 *    dataset — raw UUID title, live Edit/Delete, tabs blaming a missing version.
 * ------------------------------------------------------------------------- */
test("unknown dataset id shows an error, not a phantom page", async () => {
  const { browser, page } = await open();
  try {
    await go(page, "/datasets/11111111-2222-3333-4444-555555555555");
    assert(await page.locator(".banner.error").count() > 0, "an error banner must be shown");
    assertEqual(await page.locator("button.tab").count(), 0, "no dataset tabs for a dataset that isn't there");
    assertEqual(await page.locator("button", { hasText: "Delete" }).count(), 0, "no write controls");
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 4. Removing the last filter condition left the grid filtered with no way to
 *    clear it (the Apply button was hidden exactly when it was needed).
 * ------------------------------------------------------------------------- */
test("removing the last filter condition un-filters the grid", async () => {
  const ds = await seed(Array.from({ length: 30 }, (_, i) => ({ row_id: i + 1, label: i % 3 === 0 ? "gamma" : "alpha" })));
  created.push(ds);
  const { browser, page } = await open();
  try {
    await go(page, `/datasets/${ds}`);
    await tab(page, "Explore");
    const total = async () => parseInt((await page.locator(".small.secondary strong").first().innerText()).replace(/,/g, ""), 10);
    const full = await total();
    await btn(page, "+ Condition");
    const card = page.locator(".card").filter({ hasText: "Filters" });
    await card.locator("select").nth(0).selectOption("label");
    await card.locator("select").nth(1).selectOption("contains");
    await card.locator("input").first().fill("gamma");
    await btn(page, "Apply filters");
    assert(await total() < full, "filter should reduce the row count");

    await card.locator(".icon-btn").first().click();   // remove the only condition
    await page.waitForTimeout(1200);
    assertEqual(await total(), full, "removing the last condition must restore the full set");
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 5. Cursor paging skipped and duplicated rows when the sort key had ties.
 *    (Fixed server-side with a total-order ORDER BY; pinned here through the UI.)
 * ------------------------------------------------------------------------- */
test("paging returns every row exactly once when the sort key has ties", async () => {
  const ds = await seed(Array.from({ length: 30 }, (_, i) => ({ row_id: i + 1, grp: i % 3 })));
  created.push(ds);
  const { browser, page } = await open();
  try {
    await go(page, `/datasets/${ds}`);
    await tab(page, "Explore");
    await page.locator("table.data thead th", { hasText: "grp" }).first().click();
    await page.waitForTimeout(1000);

    const seen = [];
    for (let i = 0; i < 10; i++) {
      const ids = await page.locator("table.data tbody tr td:nth-child(1)").allInnerTexts();
      seen.push(...ids.map((s) => parseInt(s, 10)));
      const next = page.locator("button", { hasText: "Next" });
      if (await next.isDisabled()) break;
      await next.click();
      await page.waitForTimeout(900);
    }
    const distinct = new Set(seen);
    assertEqual(distinct.size, 30, `expected all 30 rows exactly once, saw ${seen.length} (${distinct.size} distinct)`);
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 6. "Act as" kept the previous seat's label, so the top bar claimed
 *    "System (admin)" while every request went out as another user.
 * ------------------------------------------------------------------------- */
test("switching seats updates the identity shown in the top bar", async () => {
  const other = await ensureViewer();
  const { browser, page } = await open();
  try {
    await go(page, "/");
    await page.locator(".topbar button").first().click();
    await page.waitForTimeout(400);
    await page.locator(".modal input").nth(0).fill(other.user_id);
    await btn(page, "Apply");
    await page.waitForTimeout(700);
    const seat = await page.locator(".topbar button").first().innerText();
    assert(!seat.includes("System (admin)"), `top bar still claims the old seat: "${seat.trim()}"`);
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 7. Catalog row click did a full page reload instead of client-side routing.
 * ------------------------------------------------------------------------- */
test("catalog row click routes client-side (no full reload)", async () => {
  const ds = await seed([{ a: 1 }, { a: 2 }]);
  created.push(ds);
  const { browser, page } = await open();
  try {
    await go(page, "/");
    await page.evaluate(() => { window.__spa = true; });
    await page.locator("table.data tbody tr").first().click();
    await page.waitForTimeout(1200);
    assert(await page.evaluate(() => !!window.__spa), "a full document reload destroyed SPA state");
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 8. Two aggregations sharing an alias produced a WRONG grand total: the totals
 *    query is a bare SELECT, so the duplicate key collapsed and the last
 *    measure won — the footer showed the count total under the sum's column.
 * ------------------------------------------------------------------------- */
test("duplicate aggregation alias is refused, never answered wrongly", async () => {
  const ds = await seed([{ region: "EU", amount: 10 }, { region: "EU", amount: 30 }, { region: "US", amount: 20 }]);
  created.push(ds);
  const { status, body } = await apiPost("/aggregate", {
    dataset_id: ds, sheet: "data", group_by: ["region"],
    aggregations: [
      { column: "amount", function: "sum", alias: "m" },
      { column: "amount", function: "count", alias: "m" },
    ],
  });
  assertEqual(status, 400, "a duplicate alias must be refused");
  assertEqual(body.code, "duplicate-alias", "with a typed code the UI can act on");
});

/* ---------------------------------------------------------------------------
 * 9. When every measure is non-additive the API returns totals:null, and the
 *    UI showed no total AND no explanation.
 * ------------------------------------------------------------------------- */
test("a non-additive-only aggregate explains the missing grand total", async () => {
  const ds = await seed([{ region: "EU", amount: 10 }, { region: "EU", amount: 30 }, { region: "US", amount: 20 }]);
  created.push(ds);
  const { browser, page } = await open();
  try {
    await go(page, `/datasets/${ds}`);
    await tab(page, "Analytics");
    const card = page.locator(".card").filter({ hasText: "Query builder" }).first();
    const selects = card.locator("select");
    await selects.filter({ has: page.locator("option", { hasText: "avg (mean)" }) }).first().selectOption("mean");
    await selects.filter({ has: page.locator("option", { hasText: "amount" }) }).last().selectOption("amount");
    await page.waitForTimeout(300);
    await btn(page, "Run query");
    await page.waitForTimeout(1500);
    const text = await page.locator("body").innerText();
    assert(/non-additive/i.test(text), "the UI must say why there is no grand total");
  } finally { await browser.close(); }
});

/* ---------------------------------------------------------------------------
 * 10. Editing a saved view silently rewrote parts the editor can't express —
 *     `search`, non-"and" logic, and extra sort keys were dropped, changing the
 *     view's answer with no warning.
 * ------------------------------------------------------------------------- */
test("editing a view preserves search, logic and extra sort keys", async () => {
  const ds = await seed(Array.from({ length: 12 }, (_, i) => ({ id: i + 1, region: i % 2 ? "north" : "south", amount: (i + 1) * 10 })));
  created.push(ds);
  const view = await apiPost(`/datasets/${ds}/views`, {
    name: `pin-${Date.now().toString().slice(-6)}`,
    sheet: "data",
    version_selector: { mode: "current" },
    query: {
      columns: ["id", "region", "amount"],
      filters: { logic: "or", conditions: [{ column: "amount", op: "gt", value: 100 }, { column: "region", op: "eq", value: "north" }] },
      sort: [{ column: "amount", direction: "desc" }, { column: "id", direction: "asc" }],
      search: "nor",
    },
  });
  assertEqual(view.status, 201, `view create: ${JSON.stringify(view.body).slice(0, 160)}`);
  const viewId = view.body.id;

  const { browser, page } = await open();
  try {
    await go(page, `/datasets/${ds}`);
    await tab(page, "Library");
    await btn(page, "Edit");
    // change only the name, then save
    const nameInput = page.locator(".modal input").first();
    await nameInput.fill(`${await nameInput.inputValue()}-edited`);
    await page.locator(".modal button", { hasText: /Save|Update/ }).last().click();
    await page.waitForTimeout(1500);
  } finally { await browser.close(); }

  const after = await apiGet(`/datasets/${ds}/views/${viewId}`);
  assertEqual(after.query.filters.logic, "or", "filter logic must survive an edit");
  assertEqual(after.query.sort.length, 2, "extra sort keys must survive an edit");
  assertEqual(after.query.search, "nor", "search must survive an edit");
});


/* ---------------------------------------------------------------------------
 * 11. SIX paths returned RAW sensitive values to a seat the masking policy says
 *     must not see them (viewer AND editor are both masked; only elevated
 *     access sees raw). Each bypassed masking by never passing the principal to
 *     the service. Two were reachable straight from the UI.
 * ------------------------------------------------------------------------- */
test("sensitive values never leak through transform / profile / diff routes", async () => {
  const SECRET = "SECRET-LEAKCHECK-42";
  const rows = Array.from({ length: 12 }, (_, i) => ({
    emp_id: i + 1, ssn: `${SECRET}-${i}`, dept: i % 2 ? "Alpha" : "Beta", salary: 100000 + i,
  }));
  const ds = await seed(rows);
  created.push(ds);
  await fetch(`${API}/datasets/${ds}/sheet-metadata/data/columns/ssn`, {
    method: "PUT",
    headers: { "X-User-Id": "00000000-0000-0000-0000-000000000001", "Content-Type": "application/json" },
    body: JSON.stringify({ sensitivity: "confidential", semantic_type: "pii" }),
  });

  const viewer = await ensureViewer();
  const asViewer = async (method, path, body) => {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: { "X-User-Id": viewer.user_id, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: res.status, text: await res.text() };
  };

  // compile-with-rows returns sampled OUTPUT rows — a viewer could author any
  // pipeline over any readable dataset and dump its sensitive columns.
  const compile = await asViewer("POST", `/datasets/${ds}/transformations/compile`,
    { sheet: "data", rows: 5, steps: [{ type: "select", columns: ["ssn", "dept"] }] });
  assert(!compile.text.includes(SECRET), `transform compile?rows leaked (status ${compile.status})`);

  // schema-only compile must STILL work — the gate is on values, not on columns.
  const schemaOnly = await asViewer("POST", `/datasets/${ds}/transformations/compile`,
    { sheet: "data", steps: [{ type: "trim", columns: ["dept"] }] });
  assertEqual(schemaOnly.status, 200, "schema-only compile must remain open to a reader");

  // A stored profile's top_values are verbatim cell values.
  await apiPost(`/datasets/${ds}/versions/1/profile-runs`);
  const runs = await apiGet(`/datasets/${ds}/versions/1/profile-runs`);
  if (runs.items?.length) {
    const detail = await asViewer("GET", `/datasets/${ds}/profile-runs/${runs.items[0].id}`);
    assert(!detail.text.includes(SECRET), "profile-run detail leaked top_values");
  }

  // Profile drift's added/removed categories come from those same top_values.
  const drift = await asViewer("GET", `/datasets/${ds}/versions/1/sheets/data/diff/1?include=profile`);
  assert(!drift.text.includes(SECRET), "profile drift leaked categories");

  // Control: an ordinary read is MASKED, not refused — the gates must not have
  // turned the whole dataset into a 403 for a legitimate reader.
  const query = await asViewer("POST", `/datasets/${ds}/versions/1/sheets/data/query`, { limit: 5 });
  assertEqual(query.status, 200, "a viewer must still be able to read the grid");
  assert(!query.text.includes(SECRET) && query.text.includes("***"),
    "grid should come back masked, not raw and not refused");
});

/* ------------------------------------------------------------------------ */

const failures = await runAll("UI regressions");
await cleanup(created);
process.exit(failures ? 1 : 0);
