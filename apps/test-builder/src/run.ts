// Runner: executes specs with @playwright/test, then masks secrets, scans for leaks and writes evidence.json.
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdirSync, readFileSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { createRequire } from 'node:module';
import { secretValues, type Target } from './config.ts';
import { maskArtifacts, scanForLeaks, walk, type LeakReport } from './secrets.ts';
import { checkTrace, type TraceCheck } from './traceCheck.ts';

const require = createRequire(import.meta.url);

export const sha256 = (buf: Buffer | string) => createHash('sha256').update(buf).digest('hex');
const sha256File = (p: string) => sha256(readFileSync(p));

export interface RunOptions {
  specs: string[];          // files or directories
  target: Target;
  rootDir: string;
  runDir: string;
  quiet?: boolean;
  /** Env vars to strip from the child (verify strips TB_LLM_* to prove no AI is involved). */
  stripEnv?: RegExp;
}

export interface JunitSummary { tests: number; failures: number; errors: number; skipped: number }

export interface Evidence {
  schema: 'test-builder/evidence@1';
  result: 'passed' | 'failed';
  exitCode: number;
  junit: JunitSummary | null;
  startedAt: string;
  endedAt: string;
  durationMs: number;
  target: { name: string; baseUrl: string; config: string; configSha256: string };
  specs: { path: string; sha256: string }[];
  tooling: { playwright: string; browser: string; node: string; platform: string };
  secretHygiene: { secretsChecked: number; beforeMasking: LeakReport; maskedFiles: string[]; afterMasking: LeakReport };
  /** Every trace rewritten by masking is re-opened in the real trace viewer (TB_TRACE_CHECK=0 skips). */
  maskedTraceChecks: TraceCheck[];
  artifacts: { path: string; bytes: number; sha256: string }[];
}

function parseJunit(file: string): JunitSummary | null {
  if (!existsSync(file)) return null;
  const xml = readFileSync(file, 'utf8');
  const m = /<testsuites[^>]*>/.exec(xml);
  if (!m) return null;
  const attr = (k: string) => Number(new RegExp(`${k}="(\\d+)"`).exec(m[0])?.[1] ?? 0);
  return { tests: attr('tests'), failures: attr('failures'), errors: attr('errors'), skipped: attr('skipped') };
}

function expandSpecs(specs: string[]): string[] {
  const out: string[] = [];
  for (const s of specs) {
    const p = resolve(s);
    if (!existsSync(p)) throw new Error(`no such spec or directory: ${s}`);
    if (statSync(p).isDirectory()) out.push(...walk(p).filter((f) => /\.spec\.ts$/.test(f)));
    else out.push(p);
  }
  if (!out.length) throw new Error(`no *.spec.ts files found in ${specs.join(', ')}`);
  return out.sort();
}

function browserBuild(): string {
  try {
    const pw = require('@playwright/test') as { chromium: { executablePath(): string } };
    const exe = pw.chromium.executablePath();
    return /ms-playwright\/([^/]+)/.exec(exe)?.[1] ?? exe;
  } catch {
    return 'unknown';
  }
}

/** Reset the target to seed state before a run, if the target declares a reset hook. */
async function applyHooks(target: Target, quiet?: boolean): Promise<void> {
  const h = target.hooks;
  if (!h?.resetUrl) return;
  const url = /^https?:\/\//.test(h.resetUrl) ? h.resetUrl : target.baseUrl + h.resetUrl;
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  for (const [k, v] of Object.entries(h.resetHeaders ?? {})) {
    const m = /^env:(\w+)$/.exec(v);
    const val = m ? process.env[m[1]!] : v;
    if (val) headers[k] = val;
  }
  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(h.resetBody ?? {}) });
  if (!res.ok) throw new Error(`reset hook ${url} -> HTTP ${res.status}`);
  if (!quiet) process.stdout.write(`  [hook] reset ${url} -> ${res.status}\n`);
}

export async function runSpecs(o: RunOptions): Promise<Evidence> {
  const specs = expandSpecs(o.specs);
  mkdirSync(o.runDir, { recursive: true });
  await applyHooks(o.target, o.quiet);
  const startedAt = new Date();
  const log = createWriteStream(join(o.runDir, 'playwright-output.log'));
  const env: NodeJS.ProcessEnv = { ...process.env, TB_RUN_DIR: o.runDir, TB_TARGET: o.target.path, FORCE_COLOR: '0' };
  if (o.stripEnv) for (const k of Object.keys(env)) if (o.stripEnv.test(k)) delete env[k];

  const cli = join(o.rootDir, 'node_modules', '@playwright', 'test', 'cli.js');
  const exitCode = await new Promise<number>((res, rej) => {
    const child = spawn(process.execPath, [cli, 'test', '--config', join(o.rootDir, 'playwright.config.ts'), ...specs], {
      cwd: o.rootDir, env, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const pipe = (d: Buffer) => { log.write(d); if (!o.quiet) process.stdout.write(d); };
    child.stdout.on('data', pipe);
    child.stderr.on('data', pipe);
    child.on('error', rej);
    child.on('close', (code) => res(code ?? 1));
  });
  await new Promise<void>((r) => log.end(r));
  const endedAt = new Date();

  // Secret hygiene: scan, mask, re-scan. Traces/HARs record fill() values and request bodies verbatim.
  const secrets = secretValues(o.target);
  const beforeMasking = await scanForLeaks(o.runDir, secrets);
  const maskedFiles = beforeMasking.occurrences ? await maskArtifacts(o.runDir, secrets) : [];
  const afterMasking = await scanForLeaks(o.runDir, secrets);
  const maskedTraceChecks: TraceCheck[] = [];
  if (process.env.TB_TRACE_CHECK !== '0') {
    for (const f of maskedFiles.filter((m) => m.endsWith('trace.zip'))) {
      const c = await checkTrace(join(o.runDir, f), o.rootDir, secrets);
      maskedTraceChecks.push({ ...c, trace: f });
      if (!o.quiet) console.log(`trace-check ${c.ok ? 'OK' : 'FAIL'} ${f} (${c.topLevelSteps} steps, ${c.stepsWithSnapshot} snapshots, ${c.problems.join('; ') || 'no problems'})`);
    }
  }
  const traceOk = maskedTraceChecks.every((c) => c.ok);

  const pkg = JSON.parse(readFileSync(join(o.rootDir, 'node_modules', '@playwright', 'test', 'package.json'), 'utf8')) as { version: string };
  const junit = parseJunit(join(o.runDir, 'junit.xml'));
  const evidencePath = join(o.runDir, 'evidence.json');
  const evidence: Evidence = {
    schema: 'test-builder/evidence@1',
    result: traceOk && exitCode === 0 && junit !== null && junit.failures === 0 && junit.errors === 0 && junit.tests > 0 ? 'passed' : 'failed',
    exitCode,
    junit,
    startedAt: startedAt.toISOString(),
    endedAt: endedAt.toISOString(),
    durationMs: endedAt.getTime() - startedAt.getTime(),
    target: { name: o.target.name, baseUrl: o.target.baseUrl, config: relative(o.rootDir, o.target.path), configSha256: sha256File(o.target.path) },
    specs: specs.map((s) => ({ path: relative(o.rootDir, s), sha256: sha256File(s) })),
    tooling: { playwright: pkg.version, browser: browserBuild(), node: process.version, platform: `${process.platform}-${process.arch}` },
    secretHygiene: { secretsChecked: secrets.length, beforeMasking, maskedFiles, afterMasking },
    maskedTraceChecks,
    artifacts: walk(o.runDir)
      .filter((f) => f !== evidencePath)
      .sort()
      .map((f) => ({ path: relative(o.runDir, f), bytes: statSync(f).size, sha256: sha256File(f) })),
  };
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  // Final check over the evidence file itself (it is outside its own hash list by construction).
  const selfScan = await scanForLeaks(o.runDir, secrets);
  if (selfScan.occurrences) throw new Error(`LEAK: ${selfScan.occurrences} secret occurrence(s) remain after masking: ${JSON.stringify(selfScan.hits)}`);
  return evidence;
}

export function summarize(e: Evidence, runDir: string): string {
  const kinds = (re: RegExp) => e.artifacts.filter((a) => re.test(a.path)).length;
  return [
    `result:    ${e.result.toUpperCase()} (exit ${e.exitCode}; junit tests=${e.junit?.tests ?? '?'} failures=${e.junit?.failures ?? '?'} errors=${e.junit?.errors ?? '?'})`,
    `run dir:   ${runDir}`,
    `artifacts: ${e.artifacts.length} hashed (traces=${kinds(/trace\.zip$/)}, hars=${kinds(/\.har$/)}, videos=${kinds(/\.webm$/)}, screenshots=${kinds(/\.png$/)}, junit=${kinds(/junit\.xml$/)})`,
    `leak scan: before masking ${e.secretHygiene.beforeMasking.occurrences} occurrence(s) in ${e.secretHygiene.beforeMasking.hits.length} place(s); ` +
      `masked ${e.secretHygiene.maskedFiles.length} file(s); after masking ${e.secretHygiene.afterMasking.occurrences} ` +
      `(scanned ${e.secretHygiene.afterMasking.scannedFiles} files + ${e.secretHygiene.afterMasking.scannedZipEntries} zip entries)`,
    `traces:    ${e.maskedTraceChecks.length} masked trace(s) re-opened in the trace viewer: ${e.maskedTraceChecks.filter((c) => c.ok).length} OK`,
    `evidence:  ${join(runDir, 'evidence.json')}`,
  ].join('\n');
}
