/**
 * A thumbnail of an app's own UI, drawn from its source, in a small browser frame. Studio has no app screenshot or
 * snapshot store, and a live preview per card would compile each app and call its backend (every call is a run),
 * so the card reads the app's entry file instead: its headings, buttons, inputs, tables, lists, charts and images
 * in source order become a to-scale wireframe with the app's real labels. No network beyond the app's detail.
 */
import { memo, useMemo } from 'react'
import { cn } from '@/shared/lib/utils'

type Block =
  | { k: 'h'; text: string; level: number }
  | { k: 'button'; text: string }
  | { k: 'inputs'; n: number }
  | { k: 'table'; cols: number }
  | { k: 'list' }
  | { k: 'chart' }
  | { k: 'img' }
  | { k: 'text' }
  | { k: 'nav' }

const TAG = /<(h[1-3]|button|Button|input|Input|textarea|Textarea|select|Select|table|Table|ul|ol|img|p|[A-Z]\w*Chart|canvas)\b/g
const CODE_LIKE = /[(){}=<>;`$|\\]|=>|aria-|className|\bon[A-Z]|\bset[A-Z]\w*|https?:|\.\w+\./

/** A label is kept only if it reads like prose: no code punctuation, short, with a letter in it. */
function clean(raw: string | undefined): string {
  const t = (raw ?? '').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim()
  if (!t || t.length > 28 || !/[A-Za-z]/.test(t) || CODE_LIKE.test(t)) return ''
  return t
}

/** Walk the opening tag from just after its name to its closing `>`, skipping quotes and `{...}` expressions
 *  (so `=>` and `>` inside onClick={...} never end the tag). Returns the attribute text and the end index. */
function readTag(src: string, from: number): { attrs: string; end: number } | null {
  let depth = 0
  let quote = ''
  for (let i = from; i < Math.min(src.length, from + 1500); i++) {
    const c = src[i]
    if (quote) {
      if (c === quote && src[i - 1] !== '\\') quote = ''
    } else if (depth > 0) {
      if (c === '"' || c === "'" || c === '`') quote = c
      else if (c === '{') depth++
      else if (c === '}') depth--
    } else if (c === '{') depth++
    else if (c === '"' || c === "'") quote = c
    else if (c === '>') return { attrs: src.slice(from, i), end: i + 1 }
  }
  return null
}

/** A string-literal prop (title="x", label='x', placeholder={"x"}) - never an expression. */
function stringProp(attrs: string, names: string): string {
  const re = new RegExp(`(?:^|\\s)(?:${names})=(?:"([^"]*)"|'([^']*)'|\\{\\s*(?:"([^"]*)"|'([^']*)')\\s*\\})`)
  const m = re.exec(attrs)
  return clean(m?.[1] ?? m?.[2] ?? m?.[3] ?? m?.[4])
}

function sketch(src: string): Block[] {
  const out: Block[] = []
  const thCount = (src.match(/<th\b/g) ?? []).length
  for (const m of src.matchAll(TAG)) {
    const tag = m[1]
    const open = readTag(src, m.index + m[0].length)
    if (!open) continue
    const selfClosed = open.attrs.trimEnd().endsWith('/')
    // Text children: only literal text directly after the opening tag, up to the next tag or expression.
    const text = selfClosed ? '' : clean(/^[^<{}]*/.exec(src.slice(open.end))?.[0])
    const label = text || stringProp(open.attrs, 'title|label|placeholder')
    const last = out[out.length - 1]
    if (/^h[1-3]$/.test(tag)) {
      if (label) out.push({ k: 'h', text: label, level: +tag[1] })
    } else if (/^button$/i.test(tag)) {
      if (out.filter((b) => b.k === 'button').length < 6) out.push({ k: 'button', text: label })
    } else if (/^(input|textarea|select)$/i.test(tag)) {
      if (last?.k === 'inputs') last.n = Math.min(3, last.n + 1)
      else out.push({ k: 'inputs', n: 1 })
    } else if (/^table$/i.test(tag)) out.push({ k: 'table', cols: Math.max(2, Math.min(5, thCount || 3)) })
    else if (/^(ul|ol)$/.test(tag)) out.push({ k: 'list' })
    else if (/Chart$|^canvas$/.test(tag)) out.push({ k: 'chart' })
    else if (tag === 'img') out.push({ k: 'img' })
    else if (tag === 'p' && last?.k !== 'text') out.push({ k: 'text' })
    if (out.length >= 10) break
  }
  return out
}

/** When real labels are scarce, fill in a believable page: title header, nav pills, a table and a list. */
function withScaffold(found: Block[], title: string): Block[] {
  if (!found.length) return []
  const out = [...found]
  if (!out.some((b) => b.k === 'h')) out.unshift({ k: 'h', text: title, level: 1 })
  out.splice(1, 0, { k: 'nav' })
  const has = (k: Block['k']) => out.some((b) => b.k === k)
  if (!has('table') && !has('list') && !has('chart')) out.push({ k: 'table', cols: 3 })
  if (!has('button')) out.push({ k: 'button', text: '' })
  if (out.length < 5) out.push({ k: 'list' })
  return out.slice(0, 10)
}

/** Consecutive buttons share a row. */
function Rows({ blocks }: { blocks: Block[] }) {
  const rows: (Block | Block[])[] = []
  for (const b of blocks) {
    const prev = rows[rows.length - 1]
    if (b.k === 'button' && Array.isArray(prev)) prev.push(b)
    else rows.push(b.k === 'button' ? [b] : b)
  }
  return (
    <>
      {rows.map((r, i) => {
        if (Array.isArray(r))
          return (
            <div key={i} className="flex flex-wrap gap-1">
              {r.slice(0, 3).map((b, j) => (
                <span key={j} className={cn('max-w-[46%] truncate rounded-kbd px-1.5 py-[1px] text-[7px] leading-[10px] font-medium', j === 0 ? 'bg-foreground/12 text-foreground/70' : 'bg-foreground/10 text-foreground/70')}>
                  {(b as { text: string }).text || <span className="inline-block h-[3px] w-6 rounded-full bg-current opacity-40 align-middle" />}
                </span>
              ))}
            </div>
          )
        const b = r
        switch (b.k) {
          case 'h':
            return (
              <span key={i} className={cn('truncate leading-tight font-semibold text-foreground/85', b.level === 1 ? 'text-[10px]' : 'text-[8.5px]')}>
                {b.text}
              </span>
            )
          case 'inputs':
            return (
              <div key={i} className="flex flex-col gap-[3px]">
                {Array.from({ length: b.n }, (_, j) => (
                  <span key={j} className="h-[9px] w-[70%] rounded-kbd bg-foreground/6 shadow-[inset_0_0_0_1px_var(--hairline)]" />
                ))}
              </div>
            )
          case 'table':
            return (
              <div key={i} className="flex flex-col gap-[2px]">
                {[0, 1, 2, 3].map((row) => (
                  <div key={row} className="grid gap-[3px]" style={{ gridTemplateColumns: `repeat(${b.cols}, minmax(0, 1fr))` }}>
                    {Array.from({ length: b.cols }, (_, c) => (
                      <span key={c} className={cn('h-[5px] rounded-full', row === 0 ? 'bg-foreground/30' : 'bg-foreground/10')} />
                    ))}
                  </div>
                ))}
              </div>
            )
          case 'list':
            return (
              <div key={i} className="flex flex-col gap-[3px]">
                {[80, 65, 72].map((w) => (
                  <span key={w} className="flex items-center gap-1">
                    <span className="size-[3px] rounded-full bg-foreground/35" />
                    <span className="h-[4px] rounded-full bg-foreground/12" style={{ width: `${w}%` }} />
                  </span>
                ))}
              </div>
            )
          case 'chart':
            return (
              <div key={i} className="flex h-6 items-end gap-[3px]">
                {[40, 65, 50, 85, 60, 95, 70].map((h, j) => (
                  <span key={j} className="flex-1 rounded-t-kbd bg-foreground/27" style={{ height: `${h}%` }} />
                ))}
              </div>
            )
          case 'nav':
            return (
              <div key={i} className="flex gap-1.5 border-b border-foreground/10 pb-1">
                {[22, 18, 26, 16].map((w, j) => (
                  <span key={j} className={cn('h-[4px] rounded-full', j === 0 ? 'bg-foreground/36' : 'bg-foreground/20')} style={{ width: w }} />
                ))}
              </div>
            )
          case 'img':
            return <span key={i} className="h-7 w-full rounded-kbd bg-foreground/8" />
          default:
            return (
              <div key={i} className="flex flex-col gap-[3px]">
                <span className="h-[4px] w-[90%] rounded-full bg-foreground/12" />
                <span className="h-[4px] w-[60%] rounded-full bg-foreground/12" />
              </div>
            )
        }
      })}
    </>
  )
}

export const AppSketch = memo(function AppSketch({ source, title }: { source: string | null; title: string }) {
  const blocks = useMemo(() => (source ? withScaffold(sketch(source), title) : []), [source, title])
  return (
    <div className="flex size-full flex-col overflow-hidden" data-testid="app-sketch" data-blocks={blocks.length}>
      <div className="flex h-4 shrink-0 items-center gap-[3px] bg-foreground/5 px-2" aria-hidden>
        <span className="size-[5px] rounded-full bg-foreground/20" />
        <span className="size-[5px] rounded-full bg-foreground/20" />
        <span className="size-[5px] rounded-full bg-foreground/20" />
        <span className="ml-1.5 truncate text-[7px] text-muted-foreground">{title}</span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-hidden px-3 pt-2.5">
        {source == null ? (
          <span className="m-auto text-[11px] text-muted-foreground">Loading…</span>
        ) : blocks.length ? (
          <Rows blocks={blocks} />
        ) : (
          <span className="m-auto text-[11px] text-muted-foreground">No UI yet</span>
        )}
      </div>
    </div>
  )
})
