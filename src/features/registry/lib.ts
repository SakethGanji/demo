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
export const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
export const short = (h: string | null | undefined, n = 12) => (h ? h.slice(0, n) : '—')
export const when = (ts: string | null | undefined) => (ts ? ts.replace('T', ' ').replace(/\+00:00$/, 'Z').slice(0, 20) : '—')

export const STAGE_STATUS: Record<Stage, Status> = { None: 'neutral', Staging: 'warning', Production: 'success', Archived: 'neutral' }
export const APPROVAL_STATUS: Record<Approval, Status> = { pending: 'warning', approved: 'success', rejected: 'danger' }
export const STAGES: Stage[] = ['None', 'Staging', 'Production', 'Archived']

export const versionLabel = (v: Pick<ModelVersion, 'model_name' | 'version'>) => `${v.model_name} v${v.version}`

export const ACTION_WORD: Record<AuditEntry['action'], string> = {
  registered: 'registered', stage_changed: 'moved', approved: 'approved', rejected: 'rejected', card_edited: 'edited card', batch_scored: 'scored a batch',
  labels_attached: 'attached labels', monitoring_configured: 'set monitoring', retrain_launched: 'launched a retrain', reason_codes_edited: 'edited reason codes',
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

/** PSI bands used across the bank: < 0.10 stable, 0.10–0.25 watch, > 0.25 shift. */
export const BAND_STATUS: Record<string, Status> = { stable: 'success', watch: 'warning', shift: 'danger', missing: 'neutral' }
export const BAND_COLOR: Record<string, string> = { stable: 'var(--status-success)', watch: 'var(--status-warning)', shift: 'var(--status-danger)', missing: 'var(--text-muted)' }
