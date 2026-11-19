import type { DatasetDetail, GymSchema, PatchChange, RunCreate } from '@/shared/api/types'

/** The Gym edits one thing: the exact body of POST /api/runs. Every control is a view onto a path in it, so the form,
 * the raw fit_kwargs editor and Vela's patches stay in step by construction. */
export type Body = RunCreate & { dataset_id: string }

export type GymState = {
  body: Body | null
  /** The run this config was loaded from (Vela patches and comparisons refer to it). */
  fromRun: string | null
  fromName?: string | null
  /** The Vela patch applied on top of fromRun's config, if any. */
  patch: { id: string; title: string; changes: PatchChange[] } | null
}

export const EMPTY_GYM: GymState = { body: null, fromRun: null, patch: null }

export const STEPS = ['Data', 'Target', 'Goal', 'Models', 'Hyperparameters', 'Bagging & Stacking', 'Search', 'Extra Data', 'Deploy', 'Review'] as const
/** Steps only Advanced mode edits (Guided skips them; the resolved call still shows what they hold). */
export const ADVANCED = new Set([4, 5, 6, 7, 8])

export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj
  for (const p of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[p]
  }
  return cur
}

/** Immutable set; `undefined` deletes the key, and emptied fit_kwargs / predictor_kwargs sub-objects stay as {}. */
export function setPath<T extends object>(obj: T, path: string, value: unknown): T {
  const parts = path.split('.')
  const out: Record<string, unknown> = Array.isArray(obj) ? ([...obj] as unknown as Record<string, unknown>) : { ...(obj as Record<string, unknown>) }
  if (parts.length === 1) {
    if (value === undefined) delete out[parts[0]]
    else out[parts[0]] = value
    return out as T
  }
  const child = out[parts[0]]
  out[parts[0]] = setPath((child && typeof child === 'object' ? child : {}) as object, parts.slice(1).join('.'), value)
  return out as T
}

export function applyChanges(body: Body, changes: PatchChange[]): Body {
  return changes.reduce((b, c) => setPath(b, c.path, c.to ?? undefined), body)
}

export function defaultBody(ds: DatasetDetail): Body {
  const label = ds.target ?? ds.profile?.target ?? null
  return {
    dataset_id: ds.id, label, problem_type: 'auto', eval_metric: null, presets: 'medium_quality', time_limit: 120,
    fit_kwargs: {}, predictor_kwargs: {}, calibrate_threshold: 'auto', feature_importance: true, drop_columns: null,
  }
}

export type Hp = Record<string, Record<string, unknown> | Record<string, unknown>[]>

/** The hyperparameters the fit will use: the body's own dict, else what the preset names ('default' is shown as AutoGluon's
 * default zoo; other portfolios such as 'zeroshot' are named, not expanded). */
export function effectiveHp(body: Body, schema: GymSchema): { hp: Hp | null; source: 'yours' | 'preset'; portfolio: string } {
  const own = body.fit_kwargs?.hyperparameters
  if (own && typeof own === 'object') return { hp: own as Hp, source: 'yours', portfolio: 'custom' }
  const presets = Array.isArray(body.presets) ? body.presets[0] : body.presets
  const named = (typeof own === 'string' ? own : (schema.preset_values[presets ?? 'medium_quality']?.hyperparameters as string | undefined)) ?? 'default'
  return { hp: named === 'default' ? (schema.default_zoo as Hp) : null, source: 'preset', portfolio: named }
}

export function variants(v: unknown): Record<string, unknown>[] {
  if (Array.isArray(v)) return v as Record<string, unknown>[]
  return [((v ?? {}) as Record<string, unknown>)]
}

/** Switch a family on or off. The first change turns the preset's zoo into an explicit hyperparameters dict. */
export function toggleFamily(body: Body, schema: GymSchema, key: string, on: boolean): Body {
  const { hp } = effectiveHp(body, schema)
  const next: Hp = JSON.parse(JSON.stringify(hp ?? schema.default_zoo))
  if (on) next[key] = next[key] ?? {}
  else delete next[key]
  return setPath(body, 'fit_kwargs.hyperparameters', next)
}

/** Set one hyperparameter of one variant of a family (undefined restores AutoGluon's default by removing the key). */
export function setHp(body: Body, schema: GymSchema, fam: string, variant: number, key: string, value: unknown): Body {
  const { hp } = effectiveHp(body, schema)
  const next: Hp = JSON.parse(JSON.stringify(hp ?? schema.default_zoo))
  const list = variants(next[fam]).map((c) => ({ ...c }))
  if (!list.length) list.push({})
  const i = Math.min(variant, list.length - 1)
  if (value === undefined) delete list[i][key]
  else list[i][key] = value
  next[fam] = Array.isArray(next[fam]) ? list : list[0]
  return setPath(body, 'fit_kwargs.hyperparameters', next)
}

export const fmtNum = (v: number) => {
  if (!Number.isFinite(v)) return String(v)
  if (v === 0) return '0'
  const a = Math.abs(v)
  if (a >= 1e4 || a < 1e-3) { const e = Math.floor(Math.log10(a)); const m = v / 10 ** e; return `${Number(m.toPrecision(3))}e${e}` }
  return String(Number(v.toPrecision(4)))
}

export const fmtSecs = (s: number) => (s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)} s` : s < 3600 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${(s / 3600).toFixed(1)} h`)

