// Click through a built app like a person would, and check the data really changed.
//   node journey.mjs BASE_URL JOURNEY.json
// The page must already be built (check.mjs) and the server running on fresh data.
// Steps (JSON list, run in order, stops at the first failure):
//   {"as": "manager"}                                      sign in as role (email <role>.user@bank.example) and open the app
//   {"tab": "Invoices"}                                    open the tab whose label starts with this
//   {"click": "Approve", "row": "INV-9"}                   press a button (inside the table row containing "row", if given)
//   {"fill": {"Amount": 500, "Vendor": "Acme"}}            fill the open form; fields by label, selects by option text
//   {"submit": true}                                       submit the open form
//   {"close": true}                                        close the open dialog
//   {"see": "text"}                                        text appears on the page (toast, table, ...)
//   {"error": "text"}                                      an error containing text is shown
//   {"row": "INV-9", "shows": "Paid"}                      that row contains the text
//   {"row": "INV-9", "no_button": "Approve"}               that row has no such button (hidden by status or role)
//   {"api": "/api/tables/invoices?invoice_number=X", "expect": {"status": "paid"}}   first row (or object) has these values; {"total": 0} for none
//   {"api": "/api/outbox", "contains": "text"}             the response contains text
import fs from "node:fs";
import { chromium } from "playwright";

const [base, file] = process.argv.slice(2);
const steps = JSON.parse(fs.readFileSync(file, "utf8"));
const results = [];
const browser = await chromium.launch();
let page = null, role = "member", problems = [];

const T = 5000;
const visibleButtons = async (scope) =>
  (await scope.getByRole("button").filter({ visible: true }).allInnerTexts()).map((s) => s.trim()).filter(Boolean);
const rowWith = (text, button) => {
  let rows = page.locator("tr").filter({ hasText: text }).filter({ visible: true });
  if (button) rows = rows.filter({ has: page.getByRole("button", { name: button, exact: true }) });
  return rows.first();
};
const dialog = () => page.getByRole("dialog").filter({ visible: true }).last();
const settle = async () => { await page.waitForLoadState("networkidle").catch(() => {}); await page.waitForTimeout(150); };
const same = (a, b) => a === b || String(a) === String(b) || (!isNaN(a) && !isNaN(b) && Number(a) === Number(b));

async function step(s) {
  if (s.as) {
    if (page) await page.close();
    role = s.as;
    page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(T);
    page.on("dialog", (d) => d.accept());  // confirm() prompts
    page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && problems.push(m.text().slice(0, 200)));
    page.on("pageerror", (e) => problems.push(String(e.message).slice(0, 200)));
    await page.addInitScript((u) => localStorage.setItem("kit.user", JSON.stringify(u)), { email: s.user || `${role}.user@bank.example`, role });
    await page.goto(base + "/", { waitUntil: "networkidle" });
    return;
  }
  if (s.tab) {
    const esc = s.tab.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    let tab = page.getByRole("tab").filter({ hasText: new RegExp(`^\\s*${esc}\\s*(\\(\\d+\\))?\\s*$`) });  // exact, or "Label (3)"
    if (!(await tab.count())) tab = page.getByRole("tab").filter({ hasText: new RegExp("^\\s*" + esc) });
    if (!(await tab.count())) throw new Error(`no tab "${s.tab}" for ${role}; tabs: ${(await page.getByRole("tab").allInnerTexts()).join(", ")}`);
    await tab.first().click(); return settle();
  }
  if (s.click) {
    const scope = s.row ? rowWith(s.row, s.click) : page;
    if (s.row && !(await scope.count())) throw new Error(`no row containing "${s.row}" with a "${s.click}" button for ${role}`
      + (await rowWith(s.row).count() ? `; that row has: ${(await visibleButtons(rowWith(s.row))).join(", ") || "no buttons"}` : "; no such row visible"));
    const b = scope.getByRole("button", { name: s.click, exact: true }).filter({ visible: true });
    if (!(await b.count())) throw new Error(`no "${s.click}" button for ${role}; visible: ${(await visibleButtons(page)).join(", ")}`);
    await b.first().click(); return settle();
  }
  if (s.fill) {
    const d = dialog();
    for (const [label, value] of Object.entries(s.fill)) {
      const f = d.getByLabel(label).first();
      if (!(await f.count())) throw new Error(`no field "${label}" in the form; fields: ${(await d.locator("label span").allInnerTexts()).join(", ")}`);
      if ((await f.evaluate((el) => el.tagName)) === "SELECT") {
        await f.selectOption({ label: String(value) }, { timeout: T }).catch(async () => {
          throw new Error(`"${label}" has no option "${value}"; options: ${(await f.locator("option").allInnerTexts()).join(", ")}`); });
      } else await f.fill(String(value));
    }
    return;
  }
  if (s.submit) { await dialog().locator('button[type="submit"]').click(); return settle(); }
  if (s.close) {  // closes the open dialog, if any (a button with no form to fill opens none)
    if (await page.getByRole("dialog").filter({ visible: true }).count()) await dialog().getByRole("button", { name: "Close" }).click();
    return settle();
  }
  if (s.see) { await page.getByText(s.see).filter({ visible: true }).first().waitFor({ timeout: T }); return; }
  if (s.error) {
    const e = page.locator(".kit-error, .kit-toast-error").filter({ hasText: s.error });
    await e.first().waitFor({ timeout: T }).catch(async () => {
      const shown = await page.locator(".kit-error, .kit-toast-error").allInnerTexts();
      throw new Error(`expected an error with "${s.error}"; shown: ${shown.join(" | ") || "none"}`); });
    return;
  }
  if (s.row && s.shows) {
    const r = rowWith(s.row);
    if (!(await r.count())) throw new Error(`no row containing "${s.row}"`);
    await r.filter({ hasText: s.shows }).waitFor({ timeout: T }).catch(async () => {
      throw new Error(`row "${s.row}" doesn't show "${s.shows}": ${(await r.innerText()).replace(/\s+/g, " ")}`); });
    return;
  }
  if (s.row && s.no_button) {
    const r = rowWith(s.row);
    if (!(await r.count())) throw new Error(`no row containing "${s.row}"`);
    if (await r.getByRole("button", { name: s.no_button, exact: true }).count()) throw new Error(`row "${s.row}" still has "${s.no_button}"`);
    return;
  }
  if (s.api) {
    const res = await fetch(base + s.api, { headers: { "X-User": `${role}.user@bank.example`, "X-Role": role } });
    const body = await res.text();
    if (!res.ok) throw new Error(`GET ${s.api} -> ${res.status} ${body.slice(0, 200)}`);
    if (s.contains && !body.includes(s.contains)) throw new Error(`GET ${s.api} doesn't contain "${s.contains}"`);
    if (s.expect) {
      const json = JSON.parse(body);
      if ("total" in s.expect && json.total !== s.expect.total) throw new Error(`GET ${s.api}: total ${json.total}, expected ${s.expect.total}`);
      const row = json.rows ? json.rows[0] : Array.isArray(json) ? json[0] : json;
      const bad = Object.entries(s.expect).filter(([k]) => k !== "total").filter(([k, v]) => !row || !same(row[k], v));
      if (bad.length) throw new Error(`GET ${s.api}: ` + bad.map(([k, v]) => `${k} is ${JSON.stringify(row?.[k])}, expected ${JSON.stringify(v)}`).join("; "));
    }
    return;
  }
  throw new Error("unknown step " + JSON.stringify(s));
}

try {
  for (const s of steps) {
    const name = JSON.stringify(s);
    problems = [];
    try {
      await step(s);
      if (problems.length) throw new Error("console: " + problems.join(" | "));
      results.push({ step: name, ok: true });
    } catch (e) {
      results.push({ step: name, ok: false, error: String(e.message).split("\n")[0].slice(0, 400), as: role });
      if (page) await page.screenshot({ path: file.replace(/\.json$/, "") + "-failed.png", fullPage: true }).catch(() => {});
      break;
    }
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify(results));
