/**
 * Pure helpers behind the understanding card and the clarify questions (PLAN-CAPGRAPH-UI §3). No React here, so the
 * wording and the answer rules can be tested. Nothing in this file shows a score: the AI's confidence is not for people.
 */
import type { ConceptSource, Understanding, UnderstandingConcept, UnderstandingQuestion, UnderstandingSummary } from '../types';

/** `concept:approval` -> `approval` (test ids and links use the slug, never the colon). */
export function slugOf(key: string): string {
  const i = key.indexOf(':');
  return i === -1 ? key : key.slice(i + 1);
}

/** `pattern:3f` -> `pattern-3f`, the way `kg-node-<key>` is written. */
export const dashKey = (key: string): string => key.replaceAll(':', '-');

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "2 sub-workflows · 1 template · 1 pattern", dropping zero parts. Counts come straight from the engine's summary. */
export function reusingText(reusing: UnderstandingSummary['reusing'] | null | undefined): string {
  const parts = [
    reusing?.subworkflows ? plural(reusing.subworkflows, 'sub-workflow', 'sub-workflows') : '',
    reusing?.templates ? plural(reusing.templates, 'template', 'templates') : '',
    reusing?.patterns ? plural(reusing.patterns, 'pattern', 'patterns') : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'Nothing reusable yet';
}

export type ChipTone = 'confirmed' | 'suggested' | 'implied';

/** Confirmed concepts are firm; the others are suggestions, and ones the AI added because another concept needs them say so. */
export function chipTone(c: Pick<UnderstandingConcept, 'confirmed' | 'source'>): ChipTone {
  if (c.confirmed) return 'confirmed';
  return (c.source as ConceptSource) === 'implied' ? 'implied' : 'suggested';
}

/** Chips for the live row: best first, at most `max`, and how many were left out. */
export function liveChips(concepts: UnderstandingConcept[] | null | undefined, max = 8): { shown: UnderstandingConcept[]; more: number } {
  const sorted = [...(concepts ?? [])].sort((a, b) => b.score - a.score || a.key.localeCompare(b.key));
  return { shown: sorted.slice(0, max), more: Math.max(0, sorted.length - max) };
}

/** The engine's blocking questions (at most 4): required and not answered yet. */
export function openQuestions(u: Pick<Understanding, 'questions'> | null | undefined): UnderstandingQuestion[] {
  return (u?.questions ?? []).filter((q) => q.required && (q.answer === null || q.answer === undefined));
}

/** The answers already given, `{question id: answer}`: handed to the app build so it does not ask them again. */
export function answeredAnswers(u: Pick<Understanding, 'questions'> | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const q of u?.questions ?? []) if (q.answer !== null && q.answer !== undefined) out[q.id] = q.answer;
  return out;
}

/** The questions to show, defensively capped at 4 required ones (the server guarantees it) plus the optional shape choice. */
export function shownQuestions(u: Pick<Understanding, 'questions'> | null | undefined, max = 4): UnderstandingQuestion[] {
  let required = 0;
  return (u?.questions ?? []).filter((q) => (q.required ? ++required <= max : true));
}

/** The concept labels of `summary.understood`, each matched to its concept (for the chip link), in the engine's order. */
export function understoodChips(u: Pick<Understanding, 'concepts' | 'summary'>): Array<{ key: string; label: string }> {
  return u.summary.understood.map((label) => {
    const c = u.concepts.find((x) => x.label === label);
    return { key: c?.key ?? `concept:${label.toLowerCase().replaceAll(/\s+/g, '_')}`, label };
  });
}

// ---------------------------------------------------------------- answers

export const DURATION_SUGGESTIONS = ['4h', '24h', '48h', '2 business days'] as const;

export const CRON_PRESETS: Array<{ label: string; value: string }> = [
  { label: 'Every day 08:00', value: '0 8 * * *' },
  { label: 'Mondays 08:00', value: '0 8 * * 1' },
  { label: 'First of month', value: '0 8 1 * *' },
];

/** At most this many options show as chips; more become a dropdown. */
export const MAX_CHIP_OPTIONS = 6;

export const NEW_ROLE = '__new_role__';

export type AnswerControl = 'role' | 'role-multi' | 'duration' | 'cron' | 'chips' | 'chips-multi' | 'select' | 'text';

/** Which control a question gets (PLAN §3.4 table). */
export function controlFor(q: Pick<UnderstandingQuestion, 'kind' | 'options' | 'multiple'>): AnswerControl {
  switch (q.kind) {
    case 'role': return q.multiple ? 'role-multi' : 'role';
    case 'duration': return 'duration';
    case 'cron': return 'cron';
    case 'api_capability': return 'select';
    case 'field': case 'choice':
      if (q.multiple) return 'chips-multi';
      return q.options.length > 0 && q.options.length <= MAX_CHIP_OPTIONS ? 'chips' : q.options.length > MAX_CHIP_OPTIONS ? 'select' : 'text';
    default: return 'text';
  }
}

/** A role question's roles: those the project already uses for this concept first, then the rest, as `{key, label}`. */
export function roleOptions(q: Pick<UnderstandingQuestion, 'concept' | 'options' | 'option_labels'>, u: Pick<Understanding, 'retrieval'> | null | undefined): Array<{ key: string; label: string }> {
  const labelOf = (key: string) => q.option_labels?.[key] ?? u?.retrieval.roles.find((r) => r.key === key)?.label ?? humanise(slugOf(key));
  const used = new Set((u?.retrieval.roles ?? []).filter((r) => r.used_for.includes(q.concept)).map((r) => r.key));
  const rank = new Map((u?.retrieval.roles ?? []).map((r, i) => [r.key, i]));
  const keys = [...q.options].sort((a, b) => Number(used.has(b)) - Number(used.has(a)) || (rank.get(a) ?? 1e6) - (rank.get(b) ?? 1e6));
  return keys.map((key) => ({ key, label: labelOf(key) }));
}

/** "Used for Escalation in 2 apps": a fact about a role, never a score. Null when no app uses it. */
export function roleHint(roleKey: string | null | undefined, u: Pick<Understanding, 'retrieval' | 'concepts'> | null | undefined): string | null {
  const r = u?.retrieval.roles.find((x) => x.key === roleKey);
  if (!r || r.apps <= 0) return null;
  const names = r.used_for.map((k) => u?.concepts.find((c) => c.key === k)?.label ?? humanise(slugOf(k)));
  return `${names.length ? `Used for ${names.join(', ')} in` : 'Used in'} ${plural(r.apps, 'app', 'apps')}`;
}

export function humanise(s: string): string {
  const t = s.replaceAll('_', ' ').trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

/** What to send for one answer. A new role goes as `{new_role}`; a multiple slot always as a list. */
export function answerPayload(q: Pick<UnderstandingQuestion, 'kind' | 'multiple'>, raw: unknown): unknown {
  if (raw && typeof raw === 'object' && !Array.isArray(raw) && 'new_role' in raw) {
    const label = String((raw as { new_role: unknown }).new_role ?? '').trim();
    return { new_role: label };
  }
  if (q.multiple) return Array.isArray(raw) ? raw : raw == null || raw === '' ? [] : [raw];
  return typeof raw === 'string' ? raw.trim() : raw;
}

/** True when the value is worth sending (non-empty). */
export function hasAnswer(v: unknown): boolean {
  if (v == null) return false;
  if (typeof v === 'string') return v.trim() !== '';
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'object') return 'new_role' in v ? String((v as { new_role: unknown }).new_role ?? '').trim() !== '' : Object.keys(v).length > 0;
  return true;
}

/** How a stored answer reads back to a person. */
export function displayValue(q: Pick<UnderstandingQuestion, 'option_labels' | 'kind'>, v: unknown): string {
  if (Array.isArray(v)) return v.map((x) => displayValue(q, x)).join(', ');
  if (v && typeof v === 'object') {
    if ('new_role' in v) return `${String((v as { new_role: unknown }).new_role)} (new role)`;
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${displayValue(q, x)}`).join(', ');
  }
  const s = String(v ?? '');
  if (q.kind === 'cron') return CRON_PRESETS.find((p) => p.value === s)?.label ?? s;
  return q.option_labels?.[s] ?? (q.kind === 'role' ? humanise(slugOf(s)) : s);
}

/** The one-line summary of a settled question: "Approval · approver role: Ops manager". */
export function answeredLine(q: Pick<UnderstandingQuestion, 'concept' | 'slot' | 'option_labels' | 'kind'>, value: unknown, conceptLabel?: string): string {
  const label = conceptLabel ?? humanise(slugOf(q.concept));
  return `${label} · ${q.slot.replaceAll('_', ' ')}: ${displayValue(q, value)}`;
}

/** "1 of 2 answered" and the share for the progress bar, over the blocking questions only. */
export function progressOf(required: Array<Pick<UnderstandingQuestion, 'id'>>, answered: ReadonlySet<string>): { done: number; total: number; ratio: number } {
  const total = required.length;
  const done = required.filter((q) => answered.has(q.id)).length;
  return { done, total, ratio: total ? done / total : 1 };
}

/** The next unanswered question after `from` (or the first one), for moving the active frame along. */
export function nextOpen(ids: string[], answered: ReadonlySet<string>, from?: string): string | null {
  const start = from ? ids.indexOf(from) + 1 : 0;
  const order = [...ids.slice(start), ...ids.slice(0, start)];
  return order.find((id) => !answered.has(id)) ?? null;
}

// ---------------------------------------------------------------- what is being reused

export interface ReusingItem {
  key: string;
  label: string;
  kind: 'subworkflow' | 'template' | 'pattern';
  /** the muted line under the label */
  line: string;
  /** a pattern this plan starts from */
  adapting: boolean;
}

/** Sub-workflows, then templates, then patterns, as the card lists them. */
export function reusingItems(u: Pick<Understanding, 'retrieval' | 'adapt_from'>): ReusingItem[] {
  const adaptKey = u.adapt_from?.pattern ?? null;
  return [
    ...u.retrieval.subworkflows.map((s): ReusingItem => ({ key: s.key, label: s.label, kind: 'subworkflow', line: s.why, adapting: false })),
    ...u.retrieval.templates.map((t): ReusingItem => ({ key: t.key, label: t.label, kind: 'template', line: t.why, adapting: false })),
    ...u.retrieval.patterns.map((p): ReusingItem => ({
      key: p.key, label: p.label, kind: 'pattern', adapting: p.adapt || p.key === adaptKey,
      line: `${p.source_project ?? 'Your organisation'} · validated ×${p.validated_count}`,
    })),
  ];
}

/** Open by default only when a validated pattern is being adapted: the "we have done this before" moment. */
export const reusingOpenByDefault = (u: Pick<Understanding, 'retrieval' | 'adapt_from'>): boolean => reusingItems(u).some((i) => i.adapting);

/** The one-line compact form: "8 concepts · reusing 2 · 2 answered". */
export function compactLine(u: Pick<Understanding, 'summary' | 'questions'>): string {
  const n = u.summary.understood.length;
  const r = u.summary.reusing;
  const reused = r.subworkflows + r.templates + r.patterns;
  const answered = u.questions.filter((q) => q.required && q.answer !== null && q.answer !== undefined).length;
  return [plural(n, 'concept', 'concepts'), reused ? `reusing ${reused}` : '', answered ? `${answered} answered` : ''].filter(Boolean).join(' · ');
}
