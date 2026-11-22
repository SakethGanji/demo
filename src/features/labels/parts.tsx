import type { ReactNode } from 'react'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Callout } from '@/shared/components/DataKit'
import { int } from '@/shared/lib/format'
import { refusal } from '@/shared/api/governance'
import type { ColStatus, LayerStatus, StatusCounts } from '@/shared/api/labels'

/** LB-C: the small parts the labeling screens share (on top of the U4 DataKit). */

export const STATUS_KEYS = ['live', 'stale', 'conflict', 'orphaned', 'unlabelled'] as const
export type StatusKey = (typeof STATUS_KEYS)[number]
export const STATUS_WORDS: Record<StatusKey, string> = {
  live: 'live: labelled, row unchanged since',
  stale: 'stale: the row changed since it was labelled; back to review, not applied',
  conflict: 'conflict: a correction and a later change of the same cell disagree',
  orphaned: 'orphaned: the row is gone from this version; kept, not applied',
  unlabelled: 'unlabelled: no label yet',
}

/** Per column carry-over counts in the five words the screens use (live = ok + in_base; base_changed counts as stale). */
export function carryOf(c: StatusCounts | ColStatus | null | undefined): Record<StatusKey, number> & { base_changed: number; disputed: number } {
  const x = (c ?? {}) as StatusCounts & ColStatus
  return { live: x.live ?? (x.ok ?? 0) + (x.in_base ?? 0), stale: (x.stale ?? 0) + (x.base_changed ?? 0), conflict: x.conflict ?? 0, orphaned: x.orphaned ?? 0,
    unlabelled: x.unlabelled ?? 0, base_changed: x.base_changed ?? 0, disputed: x.disputed ?? 0 }
}
/** The status's totals (and its columns as a list). */
export function totals(s: LayerStatus | null | undefined) { return carryOf(s?.totals ?? null) }
export function columnsOf(s: LayerStatus | null | undefined): (ReturnType<typeof carryOf> & { column: string })[] {
  const c = s?.columns
  if (!c) return []
  return Array.isArray(c) ? c.map((x) => ({ column: x.column, ...carryOf(x) })) : Object.entries(c).map(([column, x]) => ({ column, ...carryOf(x) }))
}

/** Carry-over as one stacked bar (live / stale / conflict / orphaned / unlabelled) with the counts beside it: the
 *  ChangeBar's form, five segments. Never colour alone: the counts carry the words. */
export function CarryBar({ c, testId, words = true }: { c: Partial<Record<StatusKey, number>>; testId?: string; words?: boolean }) {
  const sum = STATUS_KEYS.reduce((a, k) => a + (c[k] ?? 0), 0)
  const title = STATUS_KEYS.map((k) => `${int(c[k] ?? 0)} ${k}`).join(' · ')
  return (
    <span className="carry" title={title} data-testid={testId}>
      <span className="carry-bar" aria-hidden>{sum ? STATUS_KEYS.map((k) => (c[k] ? <i key={k} className={k} style={{ width: `${((c[k] ?? 0) / sum) * 100}%` }} /> : null)) : <i className="z" />}</span>
      {words && <span className="carry-n mono">
        <b className="live" title={STATUS_WORDS.live}>{int(c.live ?? 0)} live</b>
        {!!c.stale && <b className="stale" title={STATUS_WORDS.stale}>{int(c.stale)} stale</b>}
        {!!c.conflict && <b className="conflict" title={STATUS_WORDS.conflict}>{int(c.conflict)} conflict</b>}
        {!!c.orphaned && <b className="orphaned" title={STATUS_WORDS.orphaned}>{int(c.orphaned)} orphaned</b>}
        <span className="muted" title={STATUS_WORDS.unlabelled}>{int(c.unlabelled ?? 0)} unlabelled</span>
      </span>}
    </span>
  )
}

export const Kbd = ({ children }: { children: ReactNode }) => <kbd className="kbd">{children}</kbd>

/** A refusal as a readable line: the message, then the code. */
export function errText(e: unknown): string {
  const x = refusal(e)
  return `${x.message}${x.code ? ` (${x.code})` : ''}`
}

/** A value as text for a cell (masked values stay as the API sent them). */
export const show = (v: unknown): string => (v == null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** A shortened principal id for display ("p_ab12…" → its display name when the API sends one). */
export const who = (id?: string | null, name?: string | null) => name || id || '—'

/** The busy line inside a card (the page-level Loading is a card of its own). */
export const Busy = ({ children }: { children: ReactNode }) => <div className="row muted lbl-busy"><ResonanceWave /> {children}</div>

/** A read that failed: a route this API does not have yet (404 / 501) reads as "not available", anything else as an error. */
export function ReadErr({ err, what }: { err: string; what: string }) {
  if (/not found|not_built|not built|501/i.test(err)) return <Callout tone="neutral" title={`${what}: not available on this API yet`}>The API answered “{err}”. The screen fills in once the route is deployed.</Callout>
  return <Callout tone="danger" title={`${what} could not be read`}>{err}</Callout>
}

/** The guidelines' markdown, the small subset people write (## headings, - bullets, **bold**, `code`), as elements:
 *  no HTML is injected. */
export function Md({ text, testId }: { text: string; testId?: string }) {
  const inline = (s: string, k: number) => s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**') ? <b key={`${k}:${i}`}>{p.slice(2, -2)}</b> : p.startsWith('`') && p.endsWith('`') ? <code key={`${k}:${i}`}>{p.slice(1, -1)}</code> : p)
  const out: ReactNode[] = []
  let list: ReactNode[] = []
  const flush = () => { if (list.length) { out.push(<ul key={`u${out.length}`}>{list}</ul>); list = [] } }
  text.split('\n').forEach((l, k) => {
    const t = l.trim()
    if (/^[-*] /.test(t)) { list.push(<li key={k}>{inline(t.slice(2), k)}</li>); return }
    flush()
    if (/^#{1,6} /.test(t)) out.push(<div key={k} className="md-h">{inline(t.replace(/^#+ /, ''), k)}</div>)
    else if (t) out.push(<p key={k}>{inline(t, k)}</p>)
  })
  flush()
  return <div className="lbl-md" data-testid={testId}>{out}</div>
}
