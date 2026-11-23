#!/usr/bin/env node
// test-builder CLI:  node src/cli.ts <explore|run|verify|explore-api> [options]
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { loadTarget } from './config.ts';
import { explore, loginAndSaveState, slugify, ts } from './explore.ts';
import { ScriptedPlanner } from './planners/scripted.ts';
import { LlmPlanner, llmConfigFromEnv } from './planners/llm.ts';
import type { Planner } from './planners/types.ts';
import { runSpecs, summarize } from './run.ts';
import { loadOpenApi, generateApiSpec } from './apiExplore.ts';
import { checkTrace } from './traceCheck.ts';
import { secretValues } from './config.ts';
import { walk } from './secrets.ts';
import { approve, loadApprovals } from './approvals.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const RUNS = join(ROOT, 'runs');
const GEN = join(ROOT, 'tests', 'generated');

const USAGE = `usage:
  tb explore --target <cfg> --goal "<plain language>" [--planner llm|scripted] [--script <steps.json>] [--slug <name>] [--headed]
  tb run <spec|dir> [--target <cfg>]
  tb verify <spec> [--target <cfg>] [--times 3]
  tb explore-api --openapi <file|url> [--target <cfg>] [--slug <name>]
  tb check-trace <trace.zip|run dir>   (opens each trace in the real trace viewer and checks it with DOM assertions)
  tb approve <spec> [--note "<why>"]   (record this spec's content hash as approved to run mutating/forbidden requests)
  tb login --script <login.json> --out <state.json> [--target <cfg>]   (log in once, save a reusable storageState)`;

async function main(): Promise<number> {
  const [cmd, ...rest] = process.argv.slice(2);
  const { values: v, positionals } = parseArgs({
    args: rest,
    allowPositionals: true,
    options: {
      target: { type: 'string', default: join(ROOT, 'targets', 'demo.json') },
      goal: { type: 'string' },
      note: { type: 'string' },
      planner: { type: 'string' },
      script: { type: 'string' },
      slug: { type: 'string' },
      openapi: { type: 'string' },
      out: { type: 'string' },
      times: { type: 'string', default: '3' },
      headed: { type: 'boolean', default: false },
    },
  });
  const target = loadTarget(v.target!);

  switch (cmd) {
    case 'explore': {
      let planner: Planner;
      let goal = v.goal;
      const kind = v.planner ?? (v.script ? 'scripted' : 'llm');
      if (kind === 'scripted') {
        if (!v.script) throw new Error('--planner scripted needs --script <steps.json>');
        const s = ScriptedPlanner.fromFile(v.script);
        planner = s.planner;
        goal ??= s.script.goal;
        v.slug ??= s.script.slug;
      } else if (kind === 'llm') {
        planner = new LlmPlanner(llmConfigFromEnv());
      } else throw new Error(`unknown planner "${kind}"`);
      if (!goal) throw new Error('--goal is required');
      const slug = v.slug ?? slugify(goal);
      console.log(`explore: goal="${goal}" planner=${planner.name} target=${target.name} budget=${target.budget.maxSteps} steps/${target.budget.maxSeconds}s`);
      const r = await explore({ target, goal, planner, slug, outDir: GEN, runsDir: RUNS, rootDir: ROOT, headed: v.headed });
      const blocked = r.blocks.length;
      console.log(`\nstatus:  ${r.status}`);
      console.log(`steps:   ${r.steps.length} (${r.steps.filter((s) => s.outcome === 'ok').length} ok, ${r.steps.filter((s) => s.outcome === 'error').length} error, ${r.steps.filter((s) => s.outcome === 'blocked').length} blocked)`);
      console.log(`blocks:  ${blocked} (${r.blocks.filter((b) => b.kind === 'action').length} action, ${r.blocks.filter((b) => b.kind === 'network').length} network)`);
      console.log(`spec:    ${relative(ROOT, r.specPath)}`);
      console.log(`session: ${relative(ROOT, r.sessionPath)}`);
      return r.status === 'done' ? 0 : 2;
    }
    case 'run': {
      if (!positionals.length) throw new Error('run needs a spec file or directory');
      const runDir = join(RUNS, `${ts()}-run`);
      const e = await runSpecs({ specs: positionals, target, rootDir: ROOT, runDir });
      console.log('\n' + summarize(e, relative(ROOT, runDir)));
      return e.result === 'passed' ? 0 : 1;
    }
    case 'verify': {
      if (positionals.length !== 1) throw new Error('verify needs exactly one spec');
      const times = Number(v.times);
      const base = join(RUNS, `${ts()}-verify-${slugify(positionals[0]!.split('/').pop()!.replace(/\.spec\.ts$/, ''))}`);
      const attempts: { attempt: number; result: string; exitCode: number; tests: number | null; failures: number | null; runDir: string }[] = [];
      for (let i = 1; i <= times; i++) {
        const runDir = join(base, `attempt-${i}`);
        // No AI in the loop: the spec runs as plain Playwright and TB_LLM_* is stripped from its env.
        const e = await runSpecs({ specs: positionals, target, rootDir: ROOT, runDir, quiet: true, stripEnv: /^TB_LLM_/ });
        attempts.push({ attempt: i, result: e.result, exitCode: e.exitCode, tests: e.junit?.tests ?? null, failures: e.junit?.failures ?? null, runDir: relative(ROOT, runDir) });
        console.log(`  attempt ${i}/${times}: ${e.result.toUpperCase()} (exit ${e.exitCode}, tests=${e.junit?.tests}, failures=${e.junit?.failures}, leaks after masking=${e.secretHygiene.afterMasking.occurrences})`);
      }
      const passed = attempts.filter((a) => a.result === 'passed').length;
      const verdict = passed === times ? 'ACCEPTED' : 'REJECTED';
      mkdirSync(base, { recursive: true });
      writeFileSync(join(base, 'verify.json'), JSON.stringify({ spec: positionals[0], times, passed, verdict, aiInvolved: false, attempts }, null, 2));
      console.log(`verify: ${verdict} ${passed}/${times}  (${relative(ROOT, join(base, 'verify.json'))})`);
      return verdict === 'ACCEPTED' ? 0 : 1;
    }
    case 'explore-api': {
      if (!v.openapi) throw new Error('--openapi <file|url> is required');
      const doc = await loadOpenApi(v.openapi, target);
      const slug = v.slug ?? `api-${slugify(doc.info?.title ?? 'api')}`;
      const g = generateApiSpec(doc, target, v.openapi);
      mkdirSync(GEN, { recursive: true });
      const out = join(GEN, `${slug}.spec.ts`);
      writeFileSync(out, g.code);
      console.log(`explore-api: ${g.tests} test(s) generated -> ${relative(ROOT, out)}`);
      for (const s of g.skipped) console.log(`  skipped: ${s}`);
      return 0;
    }
    case 'check-trace': {
      if (!positionals.length) throw new Error('check-trace needs a trace.zip or a run directory');
      const zips = positionals.flatMap((p) => (p.endsWith('.zip') ? [resolve(p)] : walk(resolve(p)).filter((f) => f.endsWith('trace.zip'))));
      let bad = 0;
      for (const z of zips) {
        const c = await checkTrace(z, ROOT, secretValues(target));
        if (!c.ok) bad++;
        console.log(`${c.ok ? 'OK  ' : 'FAIL'} ${relative(ROOT, z)}: ${c.topLevelSteps} top-level steps, ${c.actions} tree items, ${c.stepsWithSnapshot} snapshots rendered, network ${c.networkEntries}, console errors ${c.consoleErrors.length}, secret visible ${c.secretVisible}, sha1 mismatches ${c.resourceNameMismatches.length} (${c.ms} ms)`);
        for (const p of c.problems) console.log(`     - ${p}`);
        for (const e of c.consoleErrors.slice(0, 5)) console.log(`       console: ${e}`);
      }
      return bad ? 1 : 0;
    }
    case 'login': {
      if (!v.script) throw new Error('login needs --script <login.json>');
      if (!v.out) throw new Error('login needs --out <state.json>');
      const s = ScriptedPlanner.fromFile(v.script);
      const out = resolve(v.out);
      await loginAndSaveState(target, s.planner.steps, out, v.headed);
      console.log(`login: saved storageState -> ${relative(ROOT, out)} (reference it from the target's auth.storageState)`);
      return 0;
    }
    case 'approve': {
      if (positionals.length !== 1) throw new Error('approve needs exactly one spec file');
      const rec = approve(positionals[0]!, ROOT, v.note);
      console.log(`approved ${relative(ROOT, rec.spec)}\n  sha256: ${rec.sha256}\n  total approved: ${Object.keys(loadApprovals(ROOT).approvals).length}`);
      console.log('  note: editing the spec changes its hash and revokes this approval.');
      return 0;
    }
    default:
      console.log(USAGE);
      return cmd ? 64 : 0;
  }
}

main().then((code) => process.exit(code), (e: Error) => {
  console.error(`error: ${e.message}`);
  process.exit(1);
});
