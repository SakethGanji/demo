// Build and check a generated page, the way the studio would load it.
//   node check.mjs APP_DIR BASE_URL
// APP_DIR/App.tsx in -> APP_DIR/ui/{index.html,app.js} out; prints checks as JSON on the last line.
// 1. type-check App.tsx against kit.tsx   2. bundle with esbuild
// 3. open it in Chromium against the running server, as every role, and click every tab.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import * as esbuild from "esbuild";
import { chromium } from "playwright";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [appDir, base] = process.argv.slice(2);
const checks = [];
const add = (layer, name, ok, error = "") => checks.push({ layer, name, ok, error });
const done = () => { console.log(JSON.stringify(checks)); process.exit(0); };

// workspace inside ui/ so node_modules resolve
const work = path.join(HERE, "work", path.basename(path.resolve(appDir)));
fs.rmSync(work, { recursive: true, force: true });
fs.mkdirSync(work, { recursive: true });
fs.copyFileSync(path.join(appDir, "App.tsx"), path.join(work, "App.tsx"));
fs.copyFileSync(path.join(HERE, "kit.tsx"), path.join(work, "kit.tsx"));
fs.copyFileSync(path.join(HERE, "main.tsx"), path.join(work, "main.tsx"));

// 1. types: catches unknown kit exports, wrong props, typos
const program = ts.createProgram([path.join(work, "main.tsx")], {
  jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020,
  moduleResolution: ts.ModuleResolutionKind.Bundler, noEmit: true, skipLibCheck: true, strict: false,
  esModuleInterop: true, lib: ["lib.es2022.d.ts", "lib.dom.d.ts"],
});
const diags = ts.getPreEmitDiagnostics(program).filter((d) => d.file?.fileName.endsWith("App.tsx"));
const fmt = (d) => {
  const { line } = d.file.getLineAndCharacterOfPosition(d.start);
  return `App.tsx line ${line + 1}: ${ts.flattenDiagnosticMessageText(d.messageText, "\n")}`;
};
add("page", "type-checks against the kit", diags.length === 0, diags.slice(0, 8).map(fmt).join("\n"));

// 2. bundle
const out = path.join(appDir, "ui");
fs.mkdirSync(out, { recursive: true });
try {
  await esbuild.build({
    entryPoints: [path.join(work, "main.tsx")], bundle: true, outfile: path.join(out, "app.js"),
    format: "iife", jsx: "automatic", target: "es2020", logLevel: "silent",
    define: { "process.env.NODE_ENV": '"production"' }, minify: true,
  });
  fs.writeFileSync(path.join(out, "index.html"),
    `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>`);
  add("page", "bundles", true);
} catch (e) {
  add("page", "bundles", false, (e.errors || []).map((x) => `${x.location?.file?.split("/").pop()} line ${x.location?.line}: ${x.text}`).join("\n") || String(e));
  done();
}

// 3. render in a real browser, as each role, clicking every tab
const app = await (await fetch(base + "/api/app")).json();
const browser = await chromium.launch();
try {
  for (const role of app.roles.length ? app.roles : ["member"]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const problems = [];
    page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && problems.push("console: " + m.text().slice(0, 300)));
    page.on("pageerror", (e) => problems.push("page error: " + String(e.message).slice(0, 300)));
    page.on("response", (r) => {
      const u = new URL(r.url());
      if (u.pathname.startsWith("/api/") && r.request().method() === "GET" && r.status() >= 400)
        r.text().then((t) => problems.push(`GET ${u.pathname}${u.search} -> ${r.status()} ${t.slice(0, 200)}`)).catch(() => {});
    });
    await page.addInitScript((u) => localStorage.setItem("kit.user", JSON.stringify(u)), { email: `${role}.user@bank.example`, role });
    await page.goto(base + "/", { waitUntil: "networkidle" });
    const tabs = await page.getByRole("tab").all();
    const tabNames = [];
    for (const t of tabs) {
      tabNames.push(await t.innerText());
      await t.click();
      await page.waitForLoadState("networkidle");
    }
    const crash = await page.evaluate(() => window.__appError || "");
    if (crash) problems.push("crash: " + crash.split("\n").slice(0, 3).join(" | "));
    const text = (await page.locator(".kit-main").innerText().catch(() => "")).trim();
    if (text.length < 40) problems.push("the page shows almost nothing (no .kit-main content; is <AppShell> used?)");
    await page.screenshot({ path: path.join(out, `screenshot-${role}.png`), fullPage: true });
    add("page", `renders for ${role}` + (tabNames.length ? ` (tabs: ${tabNames.join(", ")})` : ""), problems.length === 0, [...new Set(problems)].join("\n"));
    await page.close();
  }
} finally {
  await browser.close();
}
done();
