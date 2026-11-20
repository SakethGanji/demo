import { useEffect, useState } from 'react'
import type { Approval, AuditEntry, ModelVersion, Stage } from '@/shared/api/types'
import type { Status } from '@/shared/primitives/StatusPill'

export const bytes = (n: number | null | undefined) => {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(2)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

/** Microseconds with a sensible number of digits: 7.15 µs, 88.7 µs, 1,204 µs. */
export const us = (v: number | null | undefined) => (v == null ? '—' : v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : Math.round(v).toLocaleString())
export const ms = (v: number | null | undefined) => (v == null ? '—' : v < 10 ? v.toFixed(2) : v.toFixed(1))
export { pct } from '@/shared/lib/format'
export const short = (h: string | null | undefined, n = 12) => (h ? h.slice(0, n) : '—')
export const when = (ts: string | null | undefined) => (ts ? ts.replace('T', ' ').replace(/\+00:00$/, 'Z').slice(0, 20) : '—')

export const STAGE_STATUS: Record<Stage, Status> = { None: 'neutral', Staging: 'warning', Production: 'success', Archived: 'neutral' }
export const APPROVAL_STATUS: Record<Approval, Status> = { pending: 'warning', approved: 'success', rejected: 'danger' }
export const STAGES: Stage[] = ['None', 'Staging', 'Production', 'Archived']

/** A name as four-eyes compares it (as the API's identity_key): letters and digits only; accents, full-width forms and
 *  Cyrillic / Greek lookalikes folded ("a.l.i.c.e", "Alicé", "Аlice" are alice). Names, not authenticated identities. */
const CONFUSABLE: Record<string, string> = { а: 'a', в: 'b', ь: 'b', с: 'c', ԁ: 'd', е: 'e', ё: 'e', һ: 'h', н: 'h', і: 'i', ї: 'i', ј: 'j', к: 'k', ӏ: 'l', м: 'm', о: 'o', р: 'p', ԛ: 'q', ѕ: 's', т: 't', у: 'y', ү: 'y', х: 'x', ԝ: 'w', п: 'n', ѵ: 'v', г: 'r', α: 'a', β: 'b', ε: 'e', ζ: 'z', η: 'n', ι: 'i', κ: 'k', μ: 'm', ν: 'v', ο: 'o', ρ: 'p', τ: 't', υ: 'u', χ: 'x', γ: 'y', ω: 'w', ϲ: 'c', ı: 'i', ł: 'l', ø: 'o', đ: 'd', ħ: 'h', ß: 'ss', æ: 'ae', œ: 'oe', '0': 'o', '1': 'l' }
const UPPER: Record<string, string> = { Η: 'h', Μ: 'm', Ν: 'n', Υ: 'y', Ζ: 'z' }
export const nameKey = (s: string | null | undefined) => [...(s ?? '').normalize('NFKD')].map((c) => UPPER[c] ?? c).join('').toLowerCase()
  .split('').map((c) => CONFUSABLE[c] ?? c).join('').replace(/[^\p{L}\p{N}]|\p{Lm}/gu, '')

export const versionLabel = (v: Pick<ModelVersion, 'model_name' | 'version'>) => `${v.model_name} v${v.version}`

export const ACTION_WORD: Record<AuditEntry['action'], string> = {
  registered: 'registered', stage_changed: 'moved', approved: 'approved', rejected: 'rejected', card_edited: 'edited card', batch_scored: 'scored a batch',
  labels_attached: 'attached labels', monitoring_configured: 'set monitoring', retrain_launched: 'launched a retrain', reason_codes_edited: 'edited reason codes',
  dataset_loaded: 'loaded SQL', dataset_refreshed: 'refreshed SQL',
}

/** Who is acting: there is no sign-in, so the name is typed once and kept in this browser; every action records it. */
export function useActor(): [string, (s: string) => void] {
  const [name, setName] = useState(() => { try { return localStorage.getItem('ensemble.actor') ?? '' } catch { return '' } })
  useEffect(() => { try { localStorage.setItem('ensemble.actor', name) } catch { /* private mode */ } }, [name])
  return [name, setName]
}

export const versionParam = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('version')
export const tabParam = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('tab')
export const runParamReg = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('register')
/** F19: a model to preselect as serve_model in Register (the Model Doctor's latency prescription). */
export const serveParamReg = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('serve')
/** D2: the ladder's distillation offer opens Register with distill ticked. */
export const distillParamReg = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('distill') === '1'

/** PSI bands used across the bank: < 0.10 stable, 0.10–0.25 watch, > 0.25 shift. */
export const BAND_STATUS: Record<string, Status> = { stable: 'success', watch: 'warning', shift: 'danger', missing: 'neutral' }
export const BAND_COLOR: Record<string, string> = { stable: 'var(--status-success)', watch: 'var(--status-warning)', shift: 'var(--status-danger)', missing: 'var(--text-muted)' }
