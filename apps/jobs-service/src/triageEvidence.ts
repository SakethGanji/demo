// Structured, deterministic triage evidence: DOM diff, network diff and before/after screenshots.
// Built from the failed run's stored evidence and, when there is one, the last passing run of the same case.
import { copyFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { loadBaseline, artifactsDir, type FailureContext, type Job, type PassBaseline } from './store.ts';
import { ariaPairs, similarity } from './uiCase.ts';
import { refOf, type UiStep } from './spec.ts';

export interface DomChange { kind: 'renamed' | 'removed' | 'added'; role?: string; before: string | null; after: string | null; similarity?: number }
export interface DomDiff {
  baseline: 'last_pass' | 'expected_vs_actual' | 'n/a';
  baseline_job_id?: string;
  summary: string;
  expected?: { role?: string; name: string };
  actual?: { role: string; name: string } | null;
  changes: DomChange[];
  text_removed: string[];
  text_added: string[];
  /** The failed step differs from the same step in the last pass: the test was edited, not (only) the page. */
  step_changed?: { before: string; after: string };
}
export interface NetChange { method: string; path: string; kind: 'status_changed' | 'missing' | 'new' | 'unexpected_status' | 'status_mismatch'; expected: string | number | null; actual: string | number | null }
export interface Screenshots { before: string | null; after: string | null; before_job_id?: string | null }
export interface TriageEvidence { dom_diff: DomDiff; network_diff: NetChange[]; screenshots: Screenshots }

const key = (role: string, name: string) => `${role}\u0000${name}`;
const pathOf = (u: string) => { try { return new URL(u).pathname; } catch { return u; } };
const bad = (s: number | string) => typeof s !== 'number' || s >= 400 || s === 0;

export function domDiff(f: FailureContext, base: PassBaseline | null): DomDiff {
  if (f.kind !== 'ui') return { baseline: 'n/a', summary: 'API case: there is no DOM to compare; see network_diff.', changes: [], text_removed: [], text_added: [] };
  const ref = refOf(f.failed_step as UiStep);
  const wanted = ref?.name ?? ref?.label ?? ref?.text;
  const cur = ariaPairs(f.aria_snapshot ?? '');
  const expected = wanted !== undefined ? { ...(ref?.role ? { role: ref.role } : {}), name: wanted } : undefined;

  if (!base) {
    const best = f.candidates?.[0];
    const changes: DomChange[] = [];
    if (wanted !== undefined) {
      if (best && best.name !== wanted && best.similarity >= 0.5) changes.push({ kind: 'renamed', role: best.role, before: wanted, after: best.name, similarity: best.similarity });
      else if (!cur.some((p) => p.name === wanted && (!ref?.role || p.role === ref.role))) changes.push({ kind: 'removed', role: ref?.role, before: wanted, after: null });
    }
    const summary = changes[0]?.kind === 'renamed'
      ? `No earlier pass to compare with. Expected ${ref?.role ?? 'element'} "${wanted}", the page has ${best!.role} "${best!.name}" at the same place in the tree (similarity ${best!.similarity}).`
      : changes[0]?.kind === 'removed'
        ? `No earlier pass to compare with. Expected ${ref?.role ?? 'element'} "${wanted}"; nothing similar is on the page.`
        : wanted !== undefined
          ? `No earlier pass to compare with. "${wanted}" is on the page; the step failed for another reason (see the error).`
          : 'No earlier pass to compare with, and the failed step has no element target.';
    return { baseline: 'expected_vs_actual', summary, ...(expected ? { expected } : {}), actual: best ? { role: best.role, name: best.name } : null, changes, text_removed: [], text_added: [] };
  }

  // The passing run as it was just before the same step, not its end state.
  const at = base.before?.[String(f.failed_index)];
  const prev = ariaPairs(at?.aria_snapshot ?? base.aria_snapshot);
  const prevLines = at?.text_lines ?? base.text_lines;
  const curKeys = new Set(cur.map((p) => key(p.role, p.name)));
  const prevKeys = new Set(prev.map((p) => key(p.role, p.name)));
  const removed = prev.filter((p) => !curKeys.has(key(p.role, p.name)));
  const added = cur.filter((p) => !prevKeys.has(key(p.role, p.name)));
  const changes: DomChange[] = [];
  const usedAdded = new Set<number>();
  for (const r of removed) {
    let bi = -1, bs = 0;
    added.forEach((a, n) => { if (!usedAdded.has(n) && a.role === r.role) { const s = similarity(a.name, r.name); if (s > bs) { bs = s; bi = n; } } });
    if (bi >= 0 && bs >= 0.5) { usedAdded.add(bi); changes.push({ kind: 'renamed', role: r.role, before: r.name, after: added[bi]!.name, similarity: bs }); }
    else changes.push({ kind: 'removed', role: r.role, before: r.name, after: null });
  }
  // Leftovers: one element of a role gone and exactly one new of that role = a rename too, however different the words.
  for (const role of new Set(changes.filter((c) => c.kind === 'removed').map((c) => c.role))) {
    const rm = changes.filter((c) => c.kind === 'removed' && c.role === role);
    const ad = added.map((a, n) => ({ a, n })).filter(({ a, n }) => a.role === role && !usedAdded.has(n));
    if (rm.length === 1 && ad.length === 1) {
      usedAdded.add(ad[0]!.n);
      Object.assign(rm[0]!, { kind: 'renamed', after: ad[0]!.a.name, similarity: similarity(ad[0]!.a.name, rm[0]!.before!) });
    }
  }
  added.forEach((a, n) => { if (!usedAdded.has(n)) changes.push({ kind: 'added', role: a.role, before: null, after: a.name }); });
  // Changes that involve the failed target first.
  const rel = (c: DomChange) => (wanted !== undefined && (c.before === wanted || c.after === wanted) ? 0 : ref?.role && c.role === ref.role ? 1 : 2);
  changes.sort((a, b) => rel(a) - rel(b));
  const prevText = new Set(prevLines), curText = new Set(f.text_lines ?? []);
  const text_removed = prevLines.filter((l) => !curText.has(l)).slice(0, 8);
  const text_added = (f.text_lines ?? []).filter((l) => !prevText.has(l)).slice(0, 8);
  const wasStep = base.steps?.[f.failed_index];
  const was = wasStep ? refOf(wasStep as UiStep) : undefined;
  const wasName = was?.name ?? was?.label ?? was?.text;
  const step_changed = wasName !== undefined && wanted !== undefined && wasName !== wanted
    ? { before: `${was?.role ?? 'element'} "${wasName}"`, after: `${ref?.role ?? 'element'} "${wanted}"` } : undefined;
  const hit = changes.find((c) => wanted !== undefined && c.before === wanted);
  const summary = hit?.kind === 'renamed'
    ? `Compared with the last pass (${base.job_id}): ${hit.role ?? 'element'} "${hit.before}" is now "${hit.after}" (similarity ${hit.similarity}); ${changes.length - 1} other accessible change(s).`
    : hit
      ? `Compared with the last pass (${base.job_id}): ${hit.role ?? 'element'} "${hit.before}" is gone; ${changes.length - 1} other accessible change(s).`
      : changes.length
        ? `Compared with the last pass (${base.job_id}): the failed target is unchanged in the accessible tree, but ${changes.length} other element(s) differ.`
        : `Compared with the last pass (${base.job_id}): the accessible tree is identical${text_added.length || text_removed.length ? ', only visible text differs' : ''}.`;
  return {
    baseline: 'last_pass', baseline_job_id: base.job_id,
    summary: step_changed ? `${summary} Step ${f.failed_index} itself differs from the passing run (${step_changed.before} -> ${step_changed.after}), so the test was edited rather than the page changed.` : summary,
    ...(step_changed ? { step_changed } : {}), ...(expected ? { expected } : {}),
    actual: f.candidates?.[0] ? { role: f.candidates[0].role, name: f.candidates[0].name } : null,
    changes: changes.slice(0, 15), text_removed, text_added,
  };
}

export function networkDiff(f: FailureContext, base: PassBaseline | null): NetChange[] {
  const out: NetChange[] = [];
  const last = (rows: { method: string; url: string; status: number | string }[]) => {
    const m = new Map<string, { method: string; path: string; status: number | string }>();
    for (const r of rows) m.set(`${r.method} ${pathOf(r.url)}`, { method: r.method, path: pathOf(r.url), status: r.status });
    return m;
  };
  if (f.kind === 'api') {
    const m = /expected status ([^,]*), got (\d{3})/.exec(f.error);
    const step = f.failed_step as { method?: string; path?: string };
    if (m) out.push({ method: step.method ?? '?', path: pathOf(step.path ?? ''), kind: 'status_mismatch', expected: m[1]!.trim(), actual: Number(m[2]) });
  }
  const cur = last(f.full_network ?? f.network ?? []);
  const upto = base?.net_count?.[String(f.failed_index)];
  const prev = base ? last(upto === undefined ? base.network : base.network.slice(0, upto)) : null;
  for (const [k, c] of cur) {
    const p = prev?.get(k);
    if (p && p.status !== c.status) out.push({ method: c.method, path: c.path, kind: 'status_changed', expected: p.status, actual: c.status });
    else if (!p && bad(c.status)) out.push({ method: c.method, path: c.path, kind: 'unexpected_status', expected: '<400', actual: c.status });
    else if (!p && prev) out.push({ method: c.method, path: c.path, kind: 'new', expected: null, actual: c.status });
  }
  if (prev) for (const [k, p] of prev) if (!cur.has(k)) out.push({ method: p.method, path: p.path, kind: 'missing', expected: p.status, actual: null });
  const rank: Record<NetChange['kind'], number> = { status_mismatch: 0, status_changed: 1, unexpected_status: 2, missing: 3, new: 4 };
  return out.sort((a, b) => rank[a.kind] - rank[b.kind]).slice(0, 20);
}

/** Copy the before/after images into the triage job's artifacts so the artifacts routes can serve them. */
function screenshots(job: Job, srcJobId: string, f: FailureContext, base: PassBaseline | null): Screenshots {
  const copy = (fromJob: string, name: string | undefined, as: string): string | null => {
    if (!name) return null;
    const from = join(artifactsDir(fromJob), name);
    if (!existsSync(from)) return null;
    copyFileSync(from, join(artifactsDir(job.job_id), as));
    job.artifacts.add(as);
    return as;
  };
  const tag = f.case_id.replace(/[^\w.-]/g, '_');
  return {
    before: base ? copy(base.job_id, base.screenshot, `${tag}.before.png`) : null,
    after: copy(srcJobId, f.screenshot, `${tag}.after.png`),
    before_job_id: base?.job_id ?? null,
  };
}

export function buildEvidence(job: Job, f: FailureContext, srcJobId: string): TriageEvidence {
  const b = f.kind === 'ui' ? loadBaseline(f.case_id) : null;
  const base = b && b.job_id !== srcJobId ? b : null;
  return { dom_diff: domDiff(f, base), network_diff: networkDiff(f, base), screenshots: screenshots(job, srcJobId, f, base) };
}
