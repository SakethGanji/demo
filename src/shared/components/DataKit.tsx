import { useState, type ReactNode } from 'react'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'
import { int } from '@/shared/lib/format'

/**
 * U4: the small parts every data-platform screen shares (the dataset page, the staged upload, joins, governance,
 * Settings › Teams and Data lifecycle, the registry's inputs). Before U4 each screen had its own copy of these, so
 * the same thing looked different from card to card.
 */

/** A card header: title, an optional badge, a one-line description, and the card's actions on the right. */
export function CardHead({ title, sub, badge, actions, level = 3, testId }: { title: ReactNode; sub?: ReactNode; badge?: ReactNode; actions?: ReactNode; level?: 2 | 3; testId?: string }) {
  const H = level === 2 ? 'h2' : 'h3'
  return (
    <div className="card-head" data-testid={testId}>
      <div className="left kit-head"><H className="card-title">{title}</H>{badge}{sub != null && sub !== '' && <span className="card-sub">{sub}</span>}</div>
      {actions && <div className="card-actions">{actions}</div>}
    </div>
  )
}

/** A long id or sha shortened to a chip; the whole value is its title and a click copies it. */
export function CopyId({ value, show, n = 10, testId, className = '' }: { value: string | null | undefined; show?: ReactNode; n?: number; testId?: string; className?: string }) {
  const [done, setDone] = useState(false)
  if (!value) return <span className="muted">—</span>
  const short = show ?? (value.length > n + 4 ? `${value.slice(0, n)}…` : value)
  const copy = (e: React.MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard?.writeText(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1200) }).catch(() => {})
  }
  return (
    <button type="button" className={`id-chip ${done ? 'done' : ''} ${className}`} title={`${value} · click to copy`} onClick={copy} data-testid={testId} aria-label={`copy ${value}`}>
      {done ? 'copied' : short}
    </button>
  )
}

/** The internal ids of a thing, folded away: what a person rarely needs, one click from the screen. */
export function IdDetails({ items, label = 'ids', testId }: { items: [string, string | null | undefined][]; label?: string; testId?: string }) {
  const shown = items.filter(([, v]) => v)
  if (!shown.length) return null
  return (
    <details className="id-details" data-testid={testId}>
      <summary>{label}</summary>
      <span className="id-list">{shown.map(([k, v]) => <span key={k} className="id-kv"><span className="micro-label">{k}</span><CopyId value={v} n={14} /></span>)}</span>
    </details>
  )
}

/** Every status word of the data platform, as a pill with its tone (never colour alone: StatusPill adds a glyph). */
const TONE: Record<string, Status> = {
  active: 'success', approved: 'success', ok: 'success', pass: 'success', succeeded: 'success', used: 'success', executed: 'success',
  armed: 'success', valid: 'success', new: 'success', appended: 'success', confirmed: 'success', certified: 'success', clean: 'success',
  rewritten: 'success', pending: 'warning', warn: 'warning', watch: 'warning', suggested: 'warning', held: 'warning', disarmed: 'warning',
  delta: 'warning', not_checked: 'warning', candidate: 'warning', changed: 'warning',
  rejected: 'danger', revoked: 'danger', expired: 'danger', block: 'danger', failed: 'danger', mismatch: 'danger', deleted: 'danger',
  refused: 'danger', invalid: 'danger', purged: 'danger', drop: 'danger', shift: 'danger',
  carried: 'neutral', carry: 'neutral', withdrawn: 'neutral', archived: 'neutral', skip: 'neutral', kept: 'neutral', released: 'neutral',
  not_applicable: 'neutral', unchanged: 'neutral', none: 'neutral', stable: 'success',
  // LB-C: labeling (carry-over status, tasks, snapshots, LLM runs)
  live: 'success', stale: 'warning', conflict: 'danger', orphaned: 'neutral', unlabelled: 'neutral', open: 'success', draft: 'warning',
  paused: 'warning', closed: 'neutral', frozen: 'neutral', superseded: 'neutral', applied: 'success', running: 'warning', queued: 'warning',
  cancelled: 'neutral', fake: 'warning', off: 'neutral', gold: 'warning', disputed: 'warning', flagged: 'warning', excluded: 'danger', sent: 'success',
}
export const toneOf = (s: string | null | undefined): Status => TONE[(s ?? '').toLowerCase()] ?? 'neutral'

export function Badge({ status, children, title, testId }: { status: string; children?: ReactNode; title?: string; testId?: string }) {
  return <span className="kit-badge" title={title} data-testid={testId} data-status={status}><StatusPill status={toneOf(status)}>{children ?? status.replace(/_/g, ' ')}</StatusPill></span>
}

/** +inserted ~updated −deleted as one compact bar (each segment's width is its share of the changed rows), with the
 *  counts beside it. `base` (rows before) adds the changed share of the table to the title. */
export function ChangeBar({ add = 0, upd = 0, del = 0, base, testId, words = false }: { add?: number | null; upd?: number | null; del?: number | null; base?: number | null; testId?: string; words?: boolean }) {
  const a = add ?? 0, u = upd ?? 0, d = del ?? 0
  const sum = a + u + d
  const w = (x: number) => (sum ? `${(x / sum) * 100}%` : '0')
  const title = `${int(a)} added · ${int(u)} changed · ${int(d)} removed${base ? ` · ${((sum / base) * 100).toFixed(1)}% of ${int(base)} rows touched` : ''}`
  return (
    <span className="chg" title={title} data-testid={testId}>
      <span className="chg-bar" aria-hidden>{sum ? <><i className="a" style={{ width: w(a) }} /><i className="u" style={{ width: w(u) }} /><i className="d" style={{ width: w(d) }} /></> : <i className="z" />}</span>
      <span className="chg-n mono"><b className="a">+{int(a)}</b> <b className="u">~{int(u)}</b> <b className="d">−{int(d)}</b>{words && <span className="muted"> added · changed · removed</span>}</span>
    </span>
  )
}

/** A share m / N as a thin bar: green from 95 %, amber from 80 %, red below. */
export function CoverageBar({ m, N, value, testId, wide }: { m?: number | null; N?: number | null; value?: number | null; testId?: string; wide?: boolean }) {
  const c = value ?? (m != null && N ? m / N : null)
  if (c == null || !Number.isFinite(c)) return <span className="muted">—</span>
  const tone = c >= 0.95 ? 'ok' : c >= 0.8 ? 'warn' : 'bad'
  return (
    <span className={`cov ${wide ? 'wide' : ''}`} title={m != null && N != null ? `${int(m)} of ${int(N)} = ${c.toFixed(3)}` : c.toFixed(3)} data-testid={testId}>
      <span className="cov-track" aria-hidden><i className={tone} style={{ width: `${Math.max(0, Math.min(1, c)) * 100}%` }} /></span>
      <span className="mono cov-v">{(c * 100).toFixed(1)}%</span>
    </span>
  )
}

type Tone = 'warn' | 'danger' | 'info' | 'success' | 'neutral'
const ICON: Record<Tone, string> = { warn: '▲', danger: '✕', info: 'i', success: '✓', neutral: '·' }

/** A boxed message with a tone, a bold first line and a body: warnings, refusals, results, confirmations. */
export function Callout({ tone = 'warn', title, children, testId, role, className = '', icon = true }: { tone?: Tone; title?: ReactNode; children?: ReactNode; testId?: string; role?: string; className?: string; icon?: boolean }) {
  return (
    <div className={`callout ${tone} ${className}`} data-testid={testId} role={role ?? (tone === 'danger' ? 'alert' : undefined)}>
      {icon && <span className="callout-ico" aria-hidden>{ICON[tone]}</span>}
      <div className="callout-body">{title && <div className="callout-title">{title}</div>}{children}</div>
    </div>
  )
}

/** The result line of an action: ✓ done / ✕ refused, as a status (or an alert). */
export function Flash({ msg, testId, className = '' }: { msg: { ok: boolean; text: ReactNode } | null | undefined; testId?: string; className?: string }) {
  if (!msg) return null
  return <p className={`flash ${msg.ok ? 'ok' : 'bad'} ${className}`} role={msg.ok ? 'status' : 'alert'} data-testid={testId}><span aria-hidden>{msg.ok ? '✓' : '✕'}</span> {msg.text}</p>
}

/** A destructive button that asks once more: the first click shows "Confirm" and "Cancel" in its place. The confirm
 *  button's test id is the button's own with "-confirm". */
export function ConfirmButton({ children, confirm, onConfirm, disabled, title, className = 'btn sm', testId, warning }: {
  children: ReactNode; confirm?: ReactNode; onConfirm: () => void; disabled?: boolean; title?: string; className?: string; testId?: string; warning?: ReactNode
}) {
  const [ask, setAsk] = useState(false)
  if (!ask) return <button type="button" className={className} disabled={disabled} title={title} onClick={(e) => { e.stopPropagation(); setAsk(true) }} data-testid={testId}>{children}</button>
  return (
    <span className="confirm-ask" role="group" aria-label="confirm">
      {warning && <span className="confirm-warn">{warning}</span>}
      <button type="button" className="btn sm danger-fill" autoFocus onClick={(e) => { e.stopPropagation(); setAsk(false); onConfirm() }} data-testid={testId ? `${testId}-confirm` : undefined}>{confirm ?? 'Confirm'}</button>
      <button type="button" className="btn sm ghost" onClick={(e) => { e.stopPropagation(); setAsk(false) }}>Cancel</button>
    </span>
  )
}

/** A label above its control, with an optional hint under it. */
export function Field({ label, hint, children, grow, className = '' }: { label: ReactNode; hint?: ReactNode; children: ReactNode; grow?: boolean; className?: string }) {
  return (
    <label className={`field ${grow ? 'grow' : ''} ${className}`}>
      <span className="micro-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

/** A step indicator for the manual flows (plan → dry run → run): done, current and later steps. */
export function Steps({ steps, at, testId }: { steps: string[]; at: number; testId?: string }) {
  return (
    <ol className="kit-steps" data-testid={testId}>
      {steps.map((s, i) => <li key={s} className={i < at ? 'done' : i === at ? 'cur' : ''}><span className="kit-step-n">{i < at ? '✓' : i + 1}</span>{s}</li>)}
    </ol>
  )
}

/** Why a greyed control is greyed, under it (the same words as its tooltip). */
export function Why({ children, testId }: { children: ReactNode; testId?: string }) {
  if (!children) return null
  return <span className="why" data-testid={testId}><span aria-hidden>⊘</span> {children}</span>
}

/** A small key/value strip of headline numbers (rows, columns, …) inside a card. */
export function Stats({ items, testId }: { items: { label: ReactNode; value: ReactNode; sub?: ReactNode; testId?: string; title?: string }[]; testId?: string }) {
  return (
    <div className="kit-stats" data-testid={testId}>
      {items.map((x, i) => <div key={i} className="kit-stat" title={x.title}><span className="micro-label">{x.label}</span><b className="mono" data-testid={x.testId}>{x.value}</b>{x.sub != null && <small>{x.sub}</small>}</div>)}
    </div>
  )
}
