/**
 * Pure state rules of the app plan panel (CONTRACTS-CAPGRAPH §5): what each part is doing, which part builds next, which
 * button is the next step, when Keep is allowed, and the words on the screen. Nothing here touches the network or React.
 */
import { humanise } from '../../lib/understanding';
import type { Proposal } from '../../types';
import type { AdaptedFrom, AppBuildView, AppPlan, PartView, PlanPart } from '../appTypes';

// ---------------------------------------------------------------- is this proposal part of an app?

/**
 * True when the request on screen is an application: the AI decided (or the person chose) `shape: app` and the "build it as
 * an app?" question is no longer open, or the proposal is already one part of an app build.
 */
export function isAppProposal(p: Pick<Proposal, 'understanding' | 'app_build_id'> & { status?: Proposal['status'] } | null | undefined): boolean {
  if (!p) return false;
  if (p.app_build_id) return true;
  const u = p.understanding;
  if (!u || u.shape !== 'app') return false;
  // the person is being asked "build it as an app?" right now: let them answer it in the questions
  const asking = p.status === 'clarify' && (u.questions ?? []).some((q) => q.id === 'q_shape' && (q.answer === null || q.answer === undefined));
  return !asking;
}

// ---------------------------------------------------------------- a part's state

export type PartStage = 'waiting' | 'spec' | 'approved' | 'building' | 'built' | 'reused' | 'failed' | 'kept';

export function stageOf(p: Pick<PartView, 'status'>): PartStage {
  switch (p.status) {
    case 'reused': return 'reused';
    case 'spec_draft': return 'spec';
    case 'spec_approved': return 'approved';
    case 'building': return 'building';
    case 'ready': return 'built';
    case 'kept': return 'kept';
    case 'failed': return 'failed';
    default: return 'waiting'; // pending
  }
}

/** A part that needs no more work (built, kept, or an existing workflow stands in for it). */
export const isDone = (p: Pick<PartView, 'status'>): boolean => ['ready', 'kept', 'reused'].includes(p.status);

/** "5/5" or null: the approved tests of a built part. */
export function testsText(p: Pick<PartView, 'tests'>): string | null {
  const t = p.tests;
  if (!t || typeof t.total !== 'number' || t.total <= 0) return null;
  return `${t.passed ?? 0}/${t.total}`;
}

export function stageLabel(p: PartView): string {
  switch (stageOf(p)) {
    case 'waiting': return 'waiting';
    case 'spec': return 'plan to approve';
    case 'approved': return 'plan approved';
    case 'building': return 'building…';
    case 'built': { const t = testsText(p); return t ? `built · ${t}` : 'built'; }
    case 'kept': return 'kept';
    case 'reused': return 'reused';
    case 'failed': return 'failed';
  }
}

// ---------------------------------------------------------------- order

/** Part keys with every dependency before its dependants; plan order breaks ties. Unknown keys and cycles never loop. */
export function buildOrder(parts: Array<Pick<PartView, 'key' | 'depends_on'>>): string[] {
  const known = new Set(parts.map((p) => p.key));
  const out: string[] = [];
  const state = new Map<string, 1 | 2>();
  const byKey = new Map(parts.map((p) => [p.key, p]));
  const visit = (k: string) => {
    if (state.has(k)) return;
    state.set(k, 1);
    for (const d of byKey.get(k)?.depends_on ?? []) if (known.has(d)) visit(d);
    state.set(k, 2);
    out.push(k);
  };
  for (const p of parts) visit(p.key);
  return out;
}

/** The part the next build call builds: the first in dependency order that is neither done nor failed. */
export function nextPart(app: Pick<AppBuildView, 'parts' | 'next_part'>): PartView | null {
  const byKey = new Map(app.parts.map((p) => [p.key, p]));
  const fromServer = app.next_part ? byKey.get(app.next_part) : undefined;
  if (fromServer && !isDone(fromServer)) return fromServer; // a reused part is never built
  for (const k of buildOrder(app.parts)) {
    const p = byKey.get(k);
    if (p && !isDone(p) && p.status !== 'failed') return p;
  }
  return null;
}

/** Parts that are built, out of those that are built at all (a reused part is not counted). */
export function progressOf(parts: Array<Pick<PartView, 'status'>>): { built: number; total: number } {
  const own = parts.filter((p) => p.status !== 'reused');
  return { built: own.filter((p) => p.status === 'ready' || p.status === 'kept').length, total: own.length };
}

/** Parts whose draft plan waits for approval (and has a proposal to approve). */
export const approvableSpecs = (app: Pick<AppBuildView, 'parts'>): PartView[] =>
  app.parts.filter((p) => p.status === 'spec_draft' && !!p.proposal_id);

// ---------------------------------------------------------------- the next step

/** Which of the three step buttons is live (or what the panel is waiting on). */
export type AppStep = 'answer' | 'plan' | 'specs' | 'build' | 'keep' | 'done' | 'closed';

export function stepOf(app: Pick<AppBuildView, 'status' | 'parts'>): AppStep {
  switch (app.status) {
    case 'clarify': return 'answer';
    case 'planned': return 'plan';
    case 'specs': return app.parts.some((p) => p.status === 'spec_draft') ? 'specs' : 'build';
    case 'building': return 'build';
    case 'ready': return 'keep';
    case 'kept': return 'done';
    default: return 'closed'; // discarded
  }
}

/**
 * Keep follows the engine: `blocked` unless `can_keep`; `override` when `keep_blockers` lists a part with a red approved test
 * (the person must say "keep anyway"); else `ready`.
 */
export type KeepMode = 'blocked' | 'ready' | 'override';

export function keepMode(app: Pick<AppBuildView, 'can_keep' | 'keep_blockers'>): KeepMode {
  if (!app.can_keep) return 'blocked';
  return app.keep_blockers.some((b) => b.reason === 'tests_failing') ? 'override' : 'ready';
}

export const canKeep = (app: Pick<AppBuildView, 'can_keep' | 'keep_blockers'>): boolean => keepMode(app) !== 'blocked';

/** Titles of the parts whose approved tests are red (what "keep anyway" accepts). */
export function failingParts(app: Pick<AppBuildView, 'keep_blockers' | 'parts'>): string[] {
  const keys = new Set(app.keep_blockers.filter((b) => b.reason === 'tests_failing' && b.part_key).map((b) => b.part_key));
  return app.parts.filter((p) => keys.has(p.key)).map((p) => p.title);
}

// ---------------------------------------------------------------- words

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "5 parts · 1 table · 4 screens" */
export function summaryLine(plan: Pick<AppPlan, 'parts' | 'tables' | 'screens'> | null | undefined): string {
  if (!plan) return '';
  const bits = [plural(plan.parts.length, 'part'), plural(plan.tables?.length ?? 0, 'table')];
  const screens = plan.screens?.length ?? 0;
  if (screens) bits.push(plural(screens, 'screen'));
  return bits.join(' · ');
}

/** "Keep app (5 workflows, 1 table)": a reused part is not a new workflow. */
export function keepLabel(app: Pick<AppBuildView, 'parts' | 'plan'>): string {
  const wf = app.parts.filter((p) => p.status !== 'reused').length;
  const tables = app.plan?.tables?.length ?? 0;
  return `Keep app (${plural(wf, 'workflow')}${tables ? `, ${plural(tables, 'table')}` : ''})`;
}

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const pad = (n: number) => String(n).padStart(2, '0');

/** "0 8 * * 1" -> "Mondays 08:00" (short: "Mon 08:00"); "0 8 * * *" -> "daily 08:00". Anything else is returned as written. */
export function cronText(expr: string | null | undefined, short = false): string {
  const raw = (expr ?? '').trim();
  const f = raw.split(/\s+/);
  if (f.length !== 5) return raw;
  const [mi, h, dom, mon, dow] = f;
  if (!/^\d+$/.test(mi) || !/^\d+$/.test(h) || dom !== '*' || mon !== '*') return raw;
  const at = `${pad(Number(h))}:${pad(Number(mi))}`;
  if (dow === '*') return `daily ${at}`;
  if (!/^[0-7]$/.test(dow)) return raw;
  const day = DAYS[Number(dow) % 7];
  return short ? `${day.slice(0, 3)} ${at}` : `${day}s ${at}`;
}

/** One slot value as a person reads it: roles lose their prefix, a cron becomes a sentence, lists join. */
export function valueText(v: unknown): string {
  if (v === null || v === undefined || v === '') return 'none';
  if (Array.isArray(v)) return v.map(valueText).join(', ');
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  const s = String(v).trim();
  if (s.startsWith('role:')) return s.slice(5);
  if (/^\S+ \S+ \S+ \S+ \S+$/.test(s) && /^[\d*/,-]+$/.test(s.replaceAll(' ', ''))) return cronText(s);
  return s;
}

/** `sla.due_in` -> "Due in" (the slot name, sentence case). */
export function slotTitle(slot: string): string {
  const name = slot.includes('.') ? slot.slice(slot.indexOf('.') + 1) : slot;
  return humanise(name);
}

export interface BannerLine { slot: string; title: string; from: string; to: string }
export interface BannerModel {
  /** "Adapted from 'Case management app'" */
  headline: string;
  /** "validated by Payments Ops (21 tests)" / "validated ×2" / null */
  validated: string | null;
  lines: BannerLine[];
}

/** What the "adapted from" banner says. Only slots that really differ are listed; no score, no fit. */
export function bannerModel(a: AdaptedFrom): BannerModel {
  const by = a.source_project ? ` by ${a.source_project}` : '';
  const tests = typeof a.tests === 'number' && a.tests > 0 ? ` (${plural(a.tests, 'test')})` : '';
  const n = a.validated_count ?? 0;
  let validated: string | null = null;
  if (a.source_project || tests) validated = `validated${by}${tests}`;
  else if (n > 0) validated = `validated ×${n}`;
  const lines = (a.diff ?? [])
    .map((d) => ({ slot: d.slot, title: slotTitle(d.slot), from: valueText(d.from), to: valueText(d.to) }))
    .filter((l) => l.from !== l.to);
  return { headline: `Adapted from '${a.label}'`, validated, lines };
}

/** The titles of the parts that call this one: "called by Log an exception". */
export function calledBy(plan: Pick<AppPlan, 'parts'> | null | undefined, key: string): string[] {
  return (plan?.parts ?? []).filter((p) => p.depends_on.includes(key)).map((p) => p.title);
}

/** The second line of a part row: what kind of thing it is. */
export function partSubtitle(part: Pick<PlanPart, 'key' | 'role' | 'trigger' | 'cron' | 'interface'>, plan: Pick<AppPlan, 'parts'> | null | undefined): string {
  if (part.role === 'subworkflow') {
    const callers = calledBy(plan, part.key);
    return callers.length ? `sub-workflow · called by ${callers.join(', ')}` : 'sub-workflow';
  }
  if (part.trigger === 'cron') return part.cron ? `cron ${cronText(part.cron, true)}` : 'cron';
  const i = part.interface;
  if (i?.kind === 'custom') {
    const what = i.screen ? i.screen.split('_').pop() : '';
    return what ? `page (${what})` : 'page';
  }
  return part.trigger === 'form' ? 'form' : String(part.trigger);
}

/** `concept:sla` -> "SLA"-style label for a part's chips, from the Understanding when it names the concept. */
export function conceptLabel(key: string, labels: Record<string, string>): string {
  return labels[key] ?? humanise(key.includes(':') ? key.slice(key.indexOf(':') + 1) : key);
}

// ---------------------------------------------------------------- the mini map

export interface MapNode { key: string; title: string; kind: 'form' | 'cron' | 'page' | 'sub' | 'reuse'; x: number; y: number }
export interface MapEdge { from: string; to: string }
export interface MapTable { key: string; name: string; x: number; y: number }
export interface MiniMap { w: number; h: number; nodes: MapNode[]; edges: MapEdge[]; tables: MapTable[]; links: Array<{ from: string; table: string }> }

const MAP_W = 280;
const MAP_H = 120;

/** A static layered layout: entries on the left, the sub-workflows they call to the right, tables along the bottom. */
export function miniMap(plan: Pick<AppPlan, 'parts' | 'tables'> | null | undefined): MiniMap {
  const parts = plan?.parts ?? [];
  const tables = plan?.tables ?? [];
  const kindOf = (p: PlanPart): MapNode['kind'] =>
    p.reuse ? 'reuse' : p.role === 'subworkflow' ? 'sub' : p.trigger === 'cron' ? 'cron' : p.interface?.kind === 'custom' ? 'page' : 'form';
  const entries = parts.filter((p) => p.role === 'entry');
  const subs = parts.filter((p) => p.role !== 'entry');
  const rowH = tables.length ? 84 : MAP_H - 24;
  const place = (list: PlanPart[], x: number): MapNode[] =>
    list.map((p, i) => ({ key: p.key, title: p.title, kind: kindOf(p), x, y: 14 + (list.length === 1 ? rowH / 2 - 14 : (i * (rowH - 14)) / (list.length - 1)) }));
  const nodes = [...place(entries, 56), ...place(subs, MAP_W - 72)];
  const known = new Set(nodes.map((n) => n.key));
  const edges: MapEdge[] = [];
  for (const p of parts) for (const d of p.depends_on) if (known.has(d)) edges.push({ from: p.key, to: d });
  const tableNodes: MapTable[] = tables.map((t, i) => ({ key: t.key, name: t.name, x: tables.length === 1 ? MAP_W / 2 : 40 + (i * (MAP_W - 80)) / (tables.length - 1), y: MAP_H - 14 }));
  const links: MiniMap['links'] = [];
  for (const p of parts) for (const t of p.tables ?? []) if (tableNodes.some((n) => n.key === t) && known.has(p.key)) links.push({ from: p.key, table: t });
  return { w: MAP_W, h: MAP_H, nodes, edges, tables: tableNodes, links };
}
