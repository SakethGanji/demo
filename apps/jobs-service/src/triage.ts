// triage jobs: classify a failed case from its stored evidence. LLM first (test-builder's Gemini config), deterministic
// heuristic as fallback (and as the pre-analysis the LLM sees). Output shape is the contract's verdict.
import { scrubDeep } from '../../test-builder/src/secrets.ts';
import { completeJson, llmConfig } from './llm.ts';
import type { FailureContext, Job } from './store.ts';
import { buildEvidence, type TriageEvidence } from './triageEvidence.ts';
import { normalizeStep, refOf, type UiStep } from './spec.ts';

export type Verdict = 'bug' | 'app_changed' | 'flake' | 'environment' | 'test_bug';
export interface TriageOutput extends Partial<TriageEvidence> {
  verdict: Verdict;
  confidence: number;
  summary: string;
  evidence: { kind: string; text: string }[];
  proposed_fix?: { steps_before: unknown[]; steps_after: unknown[]; explanation: string };
  source: string;
  heuristic?: { verdict: Verdict; confidence: number };
}

const VERDICTS: Verdict[] = ['bug', 'app_changed', 'flake', 'environment', 'test_bug'];

function baseEvidence(f: FailureContext): TriageOutput['evidence'] {
  const ev: TriageOutput['evidence'] = [
    { kind: 'failed_step', text: `#${f.failed_index}: ${JSON.stringify(f.failed_step)}` },
    { kind: 'error', text: f.error },
  ];
  if (f.url) ev.push({ kind: 'page', text: `${f.url}${f.title ? ` (${f.title})` : ''}` });
  if (f.candidates?.length) ev.push({ kind: 'candidates', text: f.candidates.slice(0, 5).map((c) => `${c.role} "${c.name}" (${c.similarity})`).join(', ') });
  const bad = (f.network ?? []).filter((n) => typeof n.status !== 'number' || n.status >= 400);
  if (bad.length) ev.push({ kind: 'network', text: bad.slice(-5).map((n) => `${n.method} ${n.url} -> ${n.status}`).join('; ') });
  return ev;
}

/** Replace the failed step's target name with the candidate's. */
function fixWithName(f: FailureContext, name: string): { steps_before: unknown[]; steps_after: unknown[] } {
  const after = f.steps.map((s, i) => {
    if (i !== f.failed_index) return s;
    const st = s as UiStep & { target?: Record<string, unknown> };
    return { ...st, target: { ...(st.target ?? {}), ...(refOf(st)?.role ? { name } : refOf(st)?.label !== undefined ? { label: name } : { text: name }) } };
  });
  return { steps_before: f.steps, steps_after: after };
}

export function heuristicVerdict(f: FailureContext): TriageOutput {
  const ev = baseEvidence(f);
  const err = f.error.toLowerCase();
  const out = (verdict: Verdict, confidence: number, summary: string, fix?: TriageOutput['proposed_fix']): TriageOutput =>
    ({ verdict, confidence, summary, evidence: ev, ...(fix ? { proposed_fix: fix } : {}), source: 'heuristic' });

  if (/econnrefused|net::err_(connection|name|internet|address)|network error|enotfound|socket hang up|fetch failed|\b50[234]\b/.test(err)) {
    return out('environment', 0.75, 'The target or a dependency was unreachable or returned a gateway error; this is not a test or app defect.');
  }
  if (f.kind === 'ui') {
    const step = f.failed_step as UiStep;
    const ref = refOf(step);
    const notFound = /timeout|waiting for|not found|no element|resolved to 0/.test(err) && !/toContainText|toHaveCount|expected/.test(f.error);
    const best = f.candidates?.[0];
    const wanted = ref?.name ?? ref?.label ?? ref?.text;
    if (ref && notFound && best && wanted !== undefined && best.similarity >= 0.6 && best.name !== wanted) {
      return out('app_changed', Math.min(0.9, 0.5 + best.similarity / 2.5),
        `Step ${f.failed_index} looks for ${ref.role ?? 'element'} "${wanted}", which no longer exists; the page now has ${best.role} "${best.name}" (similarity ${best.similarity}). The label changed in the app; update the step.`,
        { ...fixWithName(f, best.name), explanation: `Rename the target of step ${f.failed_index} from "${wanted}" to "${best.name}".` });
    }
    if (/ambiguous|strict mode violation/.test(err)) return out('test_bug', 0.7, 'The locator matches more than one element; the step needs a more specific name or a "within" scope.');
    if (/tocontaintext|tohavecount|expected/.test(err)) return out('bug', 0.55, 'The page loaded and the element exists, but its content does not match what the test expects.');
    if (notFound) return out('flake', 0.4, 'The target element did not appear in time and nothing similar is on the page; re-run to rule out a flake before treating it as a bug.');
    return out('bug', 0.35, 'A step failed without a clearer signal.');
  }
  // API
  const m = /expected status [^,]*, got (\d{3})/.exec(f.error);
  if (m && (m[1] === '401' || m[1] === '403')) return out('environment', 0.55, 'The API rejected the credentials or token; check the test credentials / environment.');
  if (m && m[1] === '404' && /\{\{/.test(JSON.stringify(f.failed_step))) return out('test_bug', 0.5, 'A chained request hit 404; a saved variable may point at stale data.');
  if (/schema|expected .* got/.test(err)) return out('bug', 0.55, 'The API responded, but the response does not match the expected contract.');
  return out('bug', 0.4, 'An API expectation failed.');
}

const SYSTEM = `You are a senior QA engineer triaging ONE failed automated test case for a bank. Classify the failure.
Verdicts:
- "app_changed": the app's UI/API changed in a non-breaking way (renamed label, moved element, new copy) and the TEST must be updated. If a step targets an element by a name that no longer exists but a near-identical element exists, it is app_changed.
- "bug": the app misbehaves (wrong data, wrong status, broken flow) - a real defect to file.
- "flake": timing/nondeterminism; a re-run would likely pass.
- "environment": target down, network, credentials/test data/environment problem.
- "test_bug": the test itself is wrong (bad assertion, ambiguous locator, wrong order) and was never valid.
Reply with ONE JSON object only:
{"verdict": "...", "confidence": 0..1, "summary": "<2 sentences>", "evidence": [{"kind": "<short>", "text": "<fact from the input>"}],
 "fix": null | {"step_index": <int>, "new_step": <the corrected step object, same schema as the input steps>, "explanation": "<one sentence>"}}
Only propose a fix for app_changed or test_bug. Never invent element names: use only names present in the candidates or snapshot.`;

/** Verdict plus the structured evidence (dom_diff, network_diff, screenshots), which is always deterministic. */
export async function triage(job: Job, f: FailureContext, srcJobId: string): Promise<TriageOutput> {
  const ev = buildEvidence(job, f, srcJobId);
  const out = await classify(job, f, ev);
  return { ...out, ...ev };
}

async function classify(job: Job, f: FailureContext, ev: TriageEvidence): Promise<TriageOutput> {
  const h = heuristicVerdict(f);
  const cfg = llmConfig();
  if (!cfg) return h;
  const input = scrubDeep({
    case_kind: f.kind, steps: f.steps, failed_index: f.failed_index, failed_step: f.failed_step, error: f.error,
    page: f.url ? { url: f.url, title: f.title } : undefined, candidates_same_role: f.candidates, network: f.network?.slice(-15),
    response: f.response, aria_snapshot: f.aria_snapshot?.slice(0, 5000),
    dom_diff: ev.dom_diff, network_diff: ev.network_diff,
    deterministic_pre_analysis: { verdict: h.verdict, confidence: h.confidence, summary: h.summary },
  }, job.creds);
  try {
    const raw = await completeJson(cfg, SYSTEM, JSON.stringify(input, null, 1)) as Record<string, unknown>;
    const verdict = VERDICTS.includes(raw.verdict as Verdict) ? (raw.verdict as Verdict) : null;
    if (!verdict) throw new Error(`model returned unknown verdict ${String(raw.verdict)}`);
    const conf = typeof raw.confidence === 'number' ? Math.max(0, Math.min(1, raw.confidence)) : 0.5;
    const evidence = Array.isArray(raw.evidence)
      ? (raw.evidence as unknown[]).filter((e): e is { kind: string; text: string } => !!e && typeof (e as { kind?: unknown }).kind === 'string' && typeof (e as { text?: unknown }).text === 'string').slice(0, 8)
      : [];
    let proposed: TriageOutput['proposed_fix'];
    const fix = raw.fix as { step_index?: unknown; new_step?: unknown; explanation?: unknown } | null | undefined;
    if (fix && typeof fix.step_index === 'number' && fix.new_step && f.kind === 'ui') {
      try {
        const step = normalizeStep(fix.new_step, 'fix.new_step');
        const idx = fix.step_index;
        if (idx >= 0 && idx < f.steps.length) {
          proposed = { steps_before: f.steps, steps_after: f.steps.map((s, i) => (i === idx ? step : s)), explanation: typeof fix.explanation === 'string' ? fix.explanation : 'Model-proposed step change.' };
        }
      } catch { /* invalid model step: fall back to heuristic fix below */ }
    }
    if (!proposed && verdict === h.verdict && h.proposed_fix) proposed = h.proposed_fix;
    return {
      verdict, confidence: conf, summary: typeof raw.summary === 'string' ? raw.summary : h.summary,
      evidence: [...baseEvidence(f), ...evidence], ...(proposed ? { proposed_fix: proposed } : {}),
      source: `llm:${cfg.provider}:${cfg.model}`, heuristic: { verdict: h.verdict, confidence: h.confidence },
    };
  } catch (e) {
    return { ...h, source: 'heuristic', evidence: [...h.evidence, { kind: 'llm_unavailable', text: (e as Error).message.slice(0, 200) }] };
  }
}
