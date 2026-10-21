/** Studio UI kit — review & agent helpers (provided by Studio, refreshed every builder turn; do not edit).
 * StepDiff (readable before/after of step arrays), GuardChecks (pass/fail checklist), AiChip + changedByAiFilter
 * ("Changed by AI" marker and table filter), RowActions (real buttons for table rows) and CommandPalette (⌘K ask box). */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { cx, Spinner, Tag } from './ui'
import type { FilterChip } from './DataTable'

// ── StepDiff ─────────────────────────────────────────────────────────────────

type Rec = Record<string, unknown>

/** Steps as objects: accepts an array, a JSON string of an array, `{steps: [...]}`, or null. Strings that hold JSON are parsed. */
export function parseSteps(v: unknown): Rec[] {
  let x: unknown = v
  if (typeof x === 'string') { try { x = JSON.parse(x) } catch { const t = String(v); return t.trim() ? [{ action: t }] : [] } }
  if (x && !Array.isArray(x) && typeof x === 'object' && Array.isArray((x as Rec).steps)) x = (x as Rec).steps
  if (!Array.isArray(x)) return []
  return x.map((s) => {
    if (typeof s === 'string') { try { const p = JSON.parse(s); if (p && typeof p === 'object') return p as Rec } catch { /* plain text */ } return { action: s } }
    return s && typeof s === 'object' ? (s as Rec) : { action: String(s) }
  })
}

const pick = (s: Rec, keys: string[]) => { for (const k of keys) if (s[k] != null && s[k] !== '') return s[k]; return undefined }
const show = (v: unknown): string => {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'object' && !Array.isArray(v)) {
    // a locator such as {role:'button', name:'Verify'} or {label:'Username'} reads as: button "Verify" / label "Username"
    const o = v as Rec
    const q = (x: unknown) => `"${show(x)}"`
    if (o.role && o.name) return `${show(o.role)} ${q(o.name)}`
    for (const k of ['label', 'text', 'placeholder', 'testId', 'css', 'selector', 'name']) if (o[k] != null && Object.keys(o).length === 1) return `${k} ${q(o[k])}`
    return Object.entries(o).map(([k, x]) => `${k}=${typeof x === 'string' ? x : JSON.stringify(x)}`).join(' ')
  }
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

/** Readable parts of one step: what it does (action), what it acts on (target) and the value/expectation. */
export function describeStep(s: Rec): { action: string; target: string; value: string; group: string } {
  const action = show(pick(s, ['action', 'type', 'kind', 'op', 'method', 'step']))
  const target = show(pick(s, ['target', 'selector', 'locator', 'element', 'url', 'path', 'endpoint']))
  const value = show(pick(s, ['value', 'text', 'expect', 'expected', 'assert', 'body', 'data']))
  return { action, target, value, group: show(s.group) }
}

const isAssertion = (s: Rec) => /assert|expect|verify|check|should/i.test(show(pick(s, ['action', 'type', 'kind', 'op'])))
const canon = (v: unknown): string => (Array.isArray(v) ? `[${v.map(canon).join(',')}]` : v && typeof v === 'object' ? `{${Object.keys(v as Rec).sort().map((k) => `${JSON.stringify(k)}:${canon((v as Rec)[k])}`).join(',')}}` : JSON.stringify(v) ?? 'null')
const same = (a: Rec, b: Rec) => canon(a) === canon(b)

type DiffRow = { kind: 'same' | 'added' | 'removed' | 'changed'; before?: Rec; after?: Rec; no: number }

/** Align two step arrays: unchanged steps by LCS, then pair the leftovers in each gap as changed, add the rest as added/removed. */
export function diffSteps(before: Rec[], after: Rec[]): DiffRow[] {
  const n = before.length
  const m = after.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = same(before[i], after[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const rows: DiffRow[] = []
  let i = 0
  let j = 0
  let dels: Rec[] = []
  let adds: Rec[] = []
  const flush = () => {
    const k = Math.min(dels.length, adds.length)
    for (let x = 0; x < k; x++) rows.push({ kind: 'changed', before: dels[x], after: adds[x], no: 0 })
    for (const d of dels.slice(k)) rows.push({ kind: 'removed', before: d, no: 0 })
    for (const a of adds.slice(k)) rows.push({ kind: 'added', after: a, no: 0 })
    dels = []
    adds = []
  }
  while (i < n || j < m) {
    if (i < n && j < m && same(before[i], after[j])) { flush(); rows.push({ kind: 'same', before: before[i], after: after[j], no: 0 }); i++; j++ }
    else if (j >= m || (i < n && dp[i + 1][j] >= dp[i][j + 1])) dels.push(before[i++])
    else adds.push(after[j++])
  }
  flush()
  let no = 0
  return rows.map((r) => ({ ...r, no: r.kind === 'removed' ? 0 : ++no }))
}

const KIND_STYLE: Record<string, string> = {
  added: 'border-emerald-200 bg-emerald-50/60',
  removed: 'border-red-200 bg-red-50/60',
  changed: 'border-amber-200 bg-amber-50/50',
  same: 'border-slate-200 bg-white',
}

function Line({ s, strike }: { s: Rec; strike?: boolean }) {
  const d = describeStep(s)
  return (
    <span className={cx('min-w-0 text-sm [overflow-wrap:anywhere]', strike && 'text-red-700 line-through')}>
      {d.group && <span className="mr-1.5 rounded bg-slate-100 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">{d.group}</span>}
      <b className="whitespace-nowrap font-semibold text-slate-900">{d.action || '(step)'}</b>
      {d.target && <> <code className="rounded bg-slate-100 px-1 py-0.5 text-xs text-slate-700">{d.target}</code></>}
      {d.value && <> <span className="text-slate-600">= {d.value}</span></>}
    </span>
  )
}

/** Readable before/after of a step array: added, removed and changed steps (action, target, value); never raw JSON.
 * `before`/`after` take arrays of step objects or JSON strings. The header counts changes and states whether assertion
 * steps changed (computed from the data). Unchanged steps collapse behind a toggle. */
export function StepDiff({ before, after, title = 'Step changes', showUnchanged = false, className }:
  { before: unknown; after: unknown; title?: React.ReactNode; showUnchanged?: boolean; className?: string }) {
  const [all, setAll] = useState(showUnchanged)
  const rows = useMemo(() => diffSteps(parseSteps(before), parseSteps(after)), [before, after])
  const added = rows.filter((r) => r.kind === 'added').length
  const removed = rows.filter((r) => r.kind === 'removed').length
  const changed = rows.filter((r) => r.kind === 'changed').length
  const same_ = rows.length - added - removed - changed
  const assertionsTouched = rows.filter((r) => r.kind !== 'same' && ((r.before && isAssertion(r.before)) || (r.after && isAssertion(r.after)))).length
  const visible = all ? rows : rows.filter((r) => r.kind !== 'same')
  return (
    <div className={cx('overflow-hidden rounded-xl border border-slate-200 bg-white', className)} data-testid="step-diff">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs">
        <b className="text-slate-800">{title}</b>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {added > 0 && <Tag tone="green">+{added} added</Tag>}
          {removed > 0 && <Tag tone="red">−{removed} removed</Tag>}
          {changed > 0 && <Tag tone="amber">~{changed} changed</Tag>}
          {added + removed + changed === 0 && <Tag>no changes</Tag>}
          <Tag tone={assertionsTouched ? 'rose' : 'green'}>{assertionsTouched ? `${assertionsTouched} assertion step${assertionsTouched > 1 ? 's' : ''} changed` : 'no assertion changed'}</Tag>
        </span>
      </div>
      <ol className="m-0 flex list-none flex-col gap-1.5 p-3">
        {visible.map((r, k) => (
          <li key={k} className={cx('flex flex-col gap-0.5 rounded-lg border px-2.5 py-1.5', KIND_STYLE[r.kind])} data-diff={r.kind}>
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <span className="tabular-nums">step {r.no || '·'}</span>
              {r.kind !== 'same' && <span className="font-semibold uppercase tracking-wide text-slate-500">{r.kind}</span>}
            </div>
            <div className="flex min-w-0 flex-col gap-0.5">
              {r.kind === 'changed' && r.before && r.after ? (
                <>
                  <Line s={r.before} strike />
                  <Line s={r.after} />
                </>
              ) : <Line s={(r.after ?? r.before) as Rec} strike={r.kind === 'removed'} />}
            </div>
          </li>
        ))}
        {visible.length === 0 && <li className="text-sm text-slate-500">No step changes.</li>}
      </ol>
      {same_ > 0 && (
        <button type="button" onClick={() => setAll(!all)} className="w-full border-t border-slate-200 bg-slate-50 px-3 py-1.5 text-left text-xs font-medium text-slate-600 hover:bg-slate-100">
          {all ? 'Hide' : 'Show'} {same_} unchanged step{same_ > 1 ? 's' : ''}
        </button>
      )}
    </div>
  )
}

// ── GuardChecks ──────────────────────────────────────────────────────────────

export interface GuardCheck { id?: string; label: React.ReactNode; status: 'pass' | 'fail' | 'pending' | 'warn'; detail?: React.ReactNode }

const GUARD_ICON: Record<string, { icon: string; cls: string; sr: string }> = {
  pass: { icon: '✓', cls: 'bg-emerald-100 text-emerald-700', sr: 'passed' },
  fail: { icon: '✕', cls: 'bg-red-100 text-red-700', sr: 'failed' },
  warn: { icon: '!', cls: 'bg-amber-100 text-amber-700', sr: 'warning' },
  pending: { icon: '…', cls: 'bg-slate-100 text-slate-500', sr: 'pending' },
}

/** Checklist of pass/fail guard conditions before approving an AI change (re-run 3/3, evidence hash, assertions unchanged).
 * Each check is `{label, status: pass|fail|warn|pending, detail?}`. Build `status` from fields the API returns — never hard-code a pass. */
export function GuardChecks({ checks, title, className }: { checks: GuardCheck[]; title?: React.ReactNode; className?: string }) {
  const failing = checks.filter((c) => c.status === 'fail').length
  return (
    <div className={cx('rounded-xl border border-slate-200 bg-white', className)} data-testid="guard-checks">
      {title && <div className="border-b border-slate-200 px-3 py-2 text-xs font-semibold text-slate-800">{title}{failing > 0 && <span className="ml-2 text-red-700">{failing} failing</span>}</div>}
      <ul className="m-0 flex list-none flex-col gap-1.5 p-3">
        {checks.map((c, i) => {
          const g = GUARD_ICON[c.status] ?? GUARD_ICON.pending
          return (
            <li key={c.id ?? i} className="flex items-start gap-2 text-sm" data-status={c.status}>
              <span aria-hidden className={cx('mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold', g.cls)}>{g.icon}</span>
              <span className="sr-only">{g.sr}: </span>
              <span className="min-w-0 flex-1 text-slate-800">{c.label}{c.detail && <span className="ml-1.5 text-xs text-slate-500">{c.detail}</span>}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ── Changed by AI ────────────────────────────────────────────────────────────

/** Purple "AI" chip marking items changed by an agent; pair with changedByAiFilter. Show only when a real field says so. */
export function AiChip({ label = 'AI', title = 'Changed by AI', className }: { label?: React.ReactNode; title?: string; className?: string }) {
  return <span title={title} data-testid="ai-chip"><Tag tone="purple" className={className}>{label}</Tag></span>
}

/** A DataTable filter chip "Changed by AI": `filters={[changedByAiFilter((r) => r.changed_by === 'ai')]}` — test must read a real field. */
export function changedByAiFilter<T>(test: (row: T) => boolean, label: React.ReactNode = 'Changed by AI'): FilterChip<T> {
  return { id: 'changed-by-ai', label, test }
}

// ── RowActions ───────────────────────────────────────────────────────────────

export interface RowAction { label: React.ReactNode; onClick: () => void; tone?: 'default' | 'primary' | 'danger'; disabled?: boolean; title?: string }

/** Real buttons in a table row cell (Triage, Approve, Run) that act on that row and do not trigger onRowClick.
 * Build each closure from the row: `onClick: () => openFinding(r.id)`. */
export function RowActions({ actions, className }: { actions: RowAction[]; className?: string }) {
  const tone = { default: 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50', primary: 'border-blue-600 bg-blue-600 text-white hover:bg-blue-700', danger: 'border-red-200 bg-white text-red-700 hover:bg-red-50' }
  return (
    <span className={cx('inline-flex flex-wrap gap-1.5', className)} onClick={(e) => e.stopPropagation()}>
      {actions.map((a, i) => (
        <button key={i} type="button" disabled={a.disabled} title={a.title} onClick={(e) => { e.stopPropagation(); a.onClick() }}
          className={cx('rounded-md border px-2 py-0.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50', tone[a.tone ?? 'default'])}>
          {a.label}
        </button>
      ))}
    </span>
  )
}

// ── CommandPalette ───────────────────────────────────────────────────────────

/** Call `toggle` on ⌘K / Ctrl+K (e.g. `useCommandK(() => setAskOpen((o) => !o))`). */
export function useCommandK(toggle: () => void): void {
  const ref = useRef(toggle)
  ref.current = toggle
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); ref.current() } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}

/** ⌘K "Ask the agent" overlay with input, suggestion chips and an answer area; pair with useCommandK and a header button.
 * `onAsk(question)` returns the answer (string or node) from a real route; while it runs a spinner shows, a thrown error is shown in red. Pair with `useCommandK` and a header button. */
export function CommandPalette({ open, onClose, onAsk, title = 'Ask the agent', placeholder = 'Ask a question…', suggestions = [] }:
  { open: boolean; onClose: () => void; onAsk: (question: string) => Promise<React.ReactNode | string | void>; title?: React.ReactNode; placeholder?: string; suggestions?: string[] }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<React.ReactNode>(null)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  // The latest onClose, so an inline `() => setOpen(false)` does not re-run the effect (and steal focus) on every render.
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const handled = useRef<Event | null>(null)
  const close = (e?: { nativeEvent?: Event } | Event) => {
    const ev = e && 'nativeEvent' in e ? e.nativeEvent : (e as Event | undefined)
    if (ev) { if (handled.current === ev) return; handled.current = ev }  // one Escape reaches several listeners: close once
    closeRef.current()
  }
  const isEscape = (e: KeyboardEvent) => e.key === 'Escape' || e.key === 'Esc' || e.keyCode === 27
  useEffect(() => {
    if (!open) return
    // Inside the studio preview iframe the frame itself may not own keyboard focus yet: take it, then focus the input.
    const doc = input.current?.ownerDocument ?? document
    const win = doc.defaultView ?? window
    const before = doc.activeElement as HTMLElement | null
    try { win.focus() } catch { /* cross-origin parent: ignore */ }
    input.current?.focus()
    const raf = win.requestAnimationFrame(() => { if (!dialog.current?.contains(doc.activeElement)) input.current?.focus() })
    const onKey = (e: KeyboardEvent) => { if (isEscape(e)) { e.stopPropagation(); close(e) } }
    // capture on both the document and the window: a focused control that swallows the key cannot keep the palette open
    doc.addEventListener('keydown', onKey, true)
    win.addEventListener('keydown', onKey, true)
    // the studio preview is an iframe: when the user last clicked the studio chrome, Escape goes to the parent page
    let parentDoc: Document | null = null
    try { if (win.parent && win.parent !== win) { parentDoc = win.parent.document; parentDoc.addEventListener('keydown', onKey, true) } } catch { parentDoc = null }
    return () => {
      doc.removeEventListener('keydown', onKey, true)
      win.removeEventListener('keydown', onKey, true)
      try { parentDoc?.removeEventListener('keydown', onKey, true) } catch { /* parent went away */ }
      win.cancelAnimationFrame(raf)
      if (before && before !== doc.body && doc.contains(before)) before.focus()  // hand focus back to what opened the palette
    }
  }, [open])
  if (!open) return null
  const ask = async (text: string) => {
    const question = text.trim()
    if (!question || busy) return
    setBusy(true); setError(null); setAnswer(null)
    try { const res = await onAsk(question); setAnswer(res === undefined ? null : (res as React.ReactNode)) } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]" data-testid="command-palette"
      onKeyDown={(e) => { if (isEscape(e.nativeEvent)) { e.stopPropagation(); close(e) } }}>
      <div className="absolute inset-0 bg-slate-900/30" data-testid="palette-backdrop" onClick={close} aria-hidden />
      <div ref={dialog} role="dialog" aria-modal="true" aria-label="Ask" tabIndex={-1} className="relative w-full max-w-xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <form onSubmit={(e) => { e.preventDefault(); void ask(q) }} className="flex items-center gap-2 border-b border-slate-200 px-4 py-3">
          <span className="text-xs font-semibold text-slate-500">{title}</span>
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} aria-label="Question"
            className="min-w-0 flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400" />
          <button type="submit" disabled={busy || !q.trim()} className="rounded-md bg-blue-600 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50">Ask</button>
          <kbd className="rounded border border-slate-300 px-1 text-[10px] text-slate-500">esc</kbd>
        </form>
        <div className="max-h-[50vh] overflow-y-auto px-4 py-3 text-sm text-slate-800">
          {busy && <div className="flex items-center gap-2 text-slate-500"><Spinner /> Thinking…</div>}
          {error && <div className="text-red-700" role="alert">{error}</div>}
          {!busy && !error && answer && <div className="whitespace-pre-wrap" data-testid="palette-answer">{answer}</div>}
          {!busy && !error && !answer && suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((s) => <button key={s} type="button" onClick={() => { setQ(s); void ask(s) }} className="rounded-full border border-slate-300 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50">{s}</button>)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
