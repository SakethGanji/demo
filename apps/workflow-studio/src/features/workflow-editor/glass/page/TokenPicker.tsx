/**
 * A value token on the Page document, and its picker: click the dotted value, type a fixed value, or pick a value
 * from a step above (its fields, with this run's samples). Picking writes a reference
 * (`{{ $node["Step"].json.field }}`); the token then reads `field · step N`. Some values offer a fixed set of
 * choices instead (what happens after a failed step). Works with no AI and no run: the typed field is always there.
 */
import { Popover } from '@base-ui/react/popover'
import { useMemo, useState, type ReactNode } from 'react'
import { cx } from '@/shared/glass/cx'
import { itemPairs } from '../lib/pageRun'
import { refExpr, tokenRef, upstreamOf } from '../lib/pageDoc'
import type { PageModel, PageNode } from '../lib/pageModel'
import type { RunConn, RunPath } from '../lib/runPath'

export interface TokCtx {
  model: PageModel
  path: RunPath | null
  connections: RunConn[]
  readOnly: boolean
  onParamChange?: (node: string, path: string, value: string) => void
}

export interface TokChoice {
  value: string
  label: string
}

/** The stored value at `path` on a node (`parameters.url`, `notes`, `retry_on_fail`). */
export function rawAt(node: PageNode, path: string): unknown {
  let cur: unknown = node
  for (const k of path.split('.')) {
    if (cur === null || cur === undefined || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[k]
  }
  return cur
}

const scalar = (v: unknown) => v === undefined || v === null || typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'

export function Tok({
  node,
  path,
  value,
  label,
  ctx,
  choices,
  inputType = 'text',
  raw: rawGiven,
  source,
}: {
  node: PageNode
  /** Where the value lives on the node; '' for a token that is not stored anywhere. */
  path: string
  /** What the sentence shows. */
  value: string
  label: string
  ctx: TokCtx
  choices?: TokChoice[]
  inputType?: 'text' | 'number'
  /** The stored value, when it is not at `path` as-is (e.g. a default). */
  raw?: unknown
  /** The step a condition reads from: shown as `· step N` after the words. */
  source?: string
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const raw = rawGiven !== undefined ? rawGiven : path ? rawAt(node, path) : undefined
  const ref = tokenRef(raw)
  const canEdit = !ctx.readOnly && !!ctx.onParamChange && !!path && scalar(raw)
  const isConst = !ref && !String(raw ?? '').includes('{{')

  // a reference reads `field · step N`; `$json` is the step just before
  const srcNode = ref ? ref.source ?? upstreamOf(ctx.connections, node.name)[0] : undefined
  const srcNum = srcNode ? ctx.model.numbers[srcNode] : undefined
  const empty = !value && !ref
  const shown: ReactNode = ref ? (
    <>
      {ref.field.split('.').pop()!.replace(/_/g, ' ')}
      {srcNum && <span className="gp-src"> · step {srcNum}</span>}
    </>
  ) : empty ? (
    `[${label}]`
  ) : source && ctx.model.numbers[source] ? (
    <>
      {value}
      <span className="gp-src"> · step {ctx.model.numbers[source]}</span>
    </>
  ) : (
    value
  )

  const groups = useMemo(() => {
    if (!open || choices) return []
    const ups = upstreamOf(ctx.connections, node.name).filter((n) => ctx.model.nodes[n])
    const s = q.trim().toLowerCase()
    return ups
      .map((n) => {
        const items = ctx.path?.byNode[n]?.items ?? []
        let first: unknown = items[0]
        if (first && typeof first === 'object' && 'body' in (first as object) && (first as { body?: unknown }).body && typeof (first as { body?: unknown }).body === 'object') first = (first as { body: unknown }).body
        const opts = itemPairs(first, 12)
          .filter(([k]) => k)
          .filter(([k, v]) => !s || k.toLowerCase().includes(s) || v.toLowerCase().includes(s))
        const field = first !== items[0] ? 'body.' : ''
        return { node: n, opts: opts.map(([k, v]) => ({ field: field + k, key: k, v })) }
      })
      .filter((g) => g.opts.length)
  }, [open, choices, ctx.connections, ctx.model, ctx.path, node.name, q])

  const attrs = {
    'data-path': path || undefined,
    'data-ref': ref ? '' : undefined,
    'data-empty': empty ? '' : undefined,
    title: label,
  }
  const cls = cx('vtoken gp-tok', isConst && 'const', empty && 'empty')

  if (!canEdit) {
    return (
      <span {...attrs} data-readonly="" className={cls}>
        {shown}
      </span>
    )
  }

  const commit = (v: string) => {
    setOpen(false)
    if (v !== String(raw ?? '')) ctx.onParamChange!(node.name, path, v)
  }

  return (
    <Popover.Root
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (o) {
          setDraft(raw === undefined || raw === null ? '' : String(raw))
          setQ('')
        }
      }}
    >
      <Popover.Trigger {...attrs} render={<span />} nativeButton={false} className={cls} aria-label={`${label}: ${empty ? 'not set' : ref ? `${ref.field} from step ${srcNum ?? ''}` : value}`} onClick={(e) => e.stopPropagation()}>
        {shown}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} align="start" className="z-[70]">
          <Popover.Popup className="gl-overlay gp-picker" data-testid="glass-token-picker" data-glass-token-editor="">
            <div className="gp-picker-ttl">{choices ? label : `${label} · a fixed value, or a value from a step above`}</div>
            {choices ? (
              <div className="gp-picker-scroll" role="listbox" aria-label={label}>
                {choices.map((c) => (
                  <button key={c.value} type="button" role="option" aria-selected={String(raw ?? '') === c.value} className={cx('gp-opt', String(raw ?? '') === c.value && 'cur')} onClick={() => commit(c.value)}>
                    <span className="gp-opt-f">{c.label}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {(!choices || inputType === 'number') && (
              <form
                className="gp-picker-fixed"
                onSubmit={(e) => {
                  e.preventDefault()
                  commit(draft)
                }}
              >
                <input autoFocus type={inputType} value={draft} aria-label={label} onChange={(e) => setDraft(e.target.value)} />
                <button type="submit" className="gl-btn" data-variant="primary" data-size="sm">
                  Apply
                </button>
              </form>
            )}
            {!choices && (
              <>
                <div className="gp-picker-ttl">Values from the steps above</div>
                {ctx.path ? (
                  <>
                    <label className="gp-srch">
                      <span aria-hidden>⌕</span>
                      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search values" aria-label="Search values" />
                    </label>
                    <div className="gp-picker-scroll">
                      {groups.map((g) => (
                        <div key={g.node} className="gp-pg">
                          <div className="gp-gh">
                            <span className="gp-gh-n">{ctx.model.numbers[g.node]}</span>
                            {ctx.model.nodes[g.node]?.label || g.node}
                          </div>
                          {g.opts.map((o) => (
                            <button key={o.field} type="button" className={cx('gp-opt', ref?.source === g.node && ref.field === o.field && 'cur')} onClick={() => commit(refExpr(g.node, o.field))} data-testid="glass-token-option">
                              <span className="gp-opt-f">{o.key.replace(/_/g, ' ')}</span>
                              <span className="gp-opt-v">{o.v}</span>
                            </button>
                          ))}
                        </div>
                      ))}
                      {!groups.length && <div className="gp-picker-note">{q ? `No value matches “${q}”.` : 'The steps above have no values in this run.'}</div>}
                    </div>
                  </>
                ) : (
                  <div className="gp-picker-note">Pick a test record below to choose a value from the steps above.</div>
                )}
              </>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}
