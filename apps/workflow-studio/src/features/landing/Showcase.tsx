/**
 * The hero panel's two generated views:
 *   Code, generated   a Python module typed out line by line, with a diff counter
 *   UI, generated     an app screen assembling: skeletons resolving into the header, form and table
 * Each animates while shown; with motion off it shows its finished state.
 */
import { useEffect, useState } from 'react'
import { animate, motion, useMotionValue, useMotionValueEvent, useTransform, type MotionValue } from 'motion/react'
import { Sparkles } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

/** Runs `step` on a timer while `on` (the card is in view and motion is allowed). */
function useTicker(on: boolean, ms: number, max: number) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!on) return
    const id = window.setInterval(() => {
      if (!document.hidden) setN((v) => (v >= max ? 0 : v + 1))
    }, ms)
    return () => window.clearInterval(id)
  }, [on, ms, max])
  return n
}

/* ---------- 2. Code, generated ---------- */

type Tok = [string, string?]
const CODE: Tok[][] = [
  [['from ', 'k'], ['studio ', ''], ['import ', 'k'], ['route, table, secret', '']],
  [],
  [['@route', 'd'], ['(', ''], ['"POST /refunds/quote"', 's'], [')', '']],
  [['def ', 'k'], ['quote', 'f'], ['(amount: ', ''], ['float', 'k'], [', segment: ', ''], ['str', 'k'], ['):', '']],
  [['    limits = ', ''], ['table', 'f'], ['(', ''], ['"refund_limits"', 's'], [')', '']],
  [['    limit = limits.', ''], ['get', 'f'], ['(segment=segment)[', ''], ['"auto_limit"', 's'], [']', '']],
  [['    if ', 'k'], ['amount <= limit:', '']],
  [['        return ', 'k'], ['{', ''], ['"decision"', 's'], [': ', ''], ['"auto"', 's'], ['}', '']],
  [['    return ', 'k'], ['{', ''], ['"decision"', 's'], [': ', ''], ['"manager"', 's'], [', ', ''], ['"limit"', 's'], [': limit}', '']],
]
const TOK_CLASS: Record<string, string> = { k: 'text-hue-backend', s: 'text-hue-data', f: 'text-hue-apps', d: 'text-hue-ai', '': '' }
/** each line's length and where it starts in the typed stream (a line break counts as one character) */
const LINES = CODE.reduce<{ len: number; start: number }[]>((acc, l) => {
  const prev = acc[acc.length - 1]
  acc.push({ len: l.reduce((m, [s]) => m + s.length, 0), start: prev ? prev.start + prev.len + 1 : 0 })
  return acc
}, [])
const TOTAL = LINES[LINES.length - 1].start + LINES[LINES.length - 1].len
const TYPE_S = 4.6
const HOLD_S = 2.2

/**
 * One code line, fully rendered once and revealed by a clip that a single motion value drives: monospace, so a
 * character is exactly 1ch. Typing costs no React renders; only the active line changes state (nine times a run).
 */
function CodeLine({ i, chars, active, still }: { i: number; chars: MotionValue<number>; active: boolean; still: boolean }) {
  const { len, start } = LINES[i]
  const shown = useTransform(chars, (c) => Math.max(0, Math.min(len, Math.floor(c - start))))
  const clip = useTransform(shown, (n) => `inset(-0.3em ${len - n}ch -0.3em 0)`)
  const caret = useTransform(shown, (n) => `${n}ch`)
  return (
    <div className="flex">
      <span className="mr-4 w-4 shrink-0 text-right text-muted-foreground/50 select-none">{i + 1}</span>
      <span className="relative whitespace-pre">
        <motion.span className="inline-block" style={still ? undefined : { clipPath: clip }}>
          {CODE[i].map(([s, k], j) => (
            <span key={j} className={TOK_CLASS[k ?? '']}>
              {s}
            </span>
          ))}
        </motion.span>
        {active && <motion.span className="landing-caret" style={{ x: caret }} />}
      </span>
    </div>
  )
}

function CodeGen({ on, still }: { on: boolean; still: boolean }) {
  const chars = useMotionValue(still ? TOTAL : 0)
  const [line, setLine] = useState(still ? LINES.length - 1 : 0)
  const [typing, setTyping] = useState(!still)
  useMotionValueEvent(chars, 'change', (c) => {
    let l = 0
    while (l < LINES.length - 1 && c >= LINES[l + 1].start) l++
    setLine(l)
    setTyping(c < TOTAL)
  })
  useEffect(() => {
    if (!on || still) return
    const a = animate(chars, [0, TOTAL], { duration: TYPE_S, ease: 'linear', repeat: Infinity, repeatDelay: HOLD_S })
    return () => a.stop()
  }, [on, still, chars])
  const cur = still ? LINES.length - 1 : line
  const busy = !still && typing
  return (
    <div className="well w-full overflow-hidden">
      <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5 text-[12px]">
        <Sparkles className="size-3.5 text-hue-ai" />
        <span className="font-medium">{busy ? 'Generating refunds.py' : 'refunds.py'}</span>
        <span className="ml-auto font-mono text-success-text">+{busy ? cur : LINES.length}</span>
        <span className="font-mono text-destructive">{'\u22120'}</span>
      </div>
      <pre className="min-h-[15.5rem] px-4 py-3 font-mono text-[12.5px] leading-[1.75]">
        {LINES.slice(0, cur + 1).map((_, i) => (
          <CodeLine key={i} i={i} chars={chars} active={busy && i === cur} still={still} />
        ))}
      </pre>
    </div>
  )
}

/* ---------- 3. UI, generated ---------- */

const ROWS = [
  ['ana@bank.example', '1,250', 'Manager'],
  ['li@bank.example', '120', 'Auto'],
  ['sam@bank.example', '480', 'Auto'],
]

function Skel({ ready, className, children }: { ready: boolean; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('relative', className)}>
      <motion.div initial={false} animate={{ opacity: ready ? 1 : 0, y: ready ? 0 : 4 }} transition={{ duration: 0.35 }}>
        {children}
      </motion.div>
      {!ready && <span className="landing-shimmer absolute inset-0 rounded-ctl" />}
    </div>
  )
}

function UIGen({ on, still }: { on: boolean; still: boolean }) {
  const t = useTicker(on && !still, 420, 14)
  const s = still ? 99 : t
  return (
    <div className="well w-full overflow-hidden p-4">
      <Skel ready={s >= 1} className="mb-4">
        <div className="flex items-center gap-2.5">
          <span className="brand-fill size-7 rounded-ctl" />
          <span className="text-[14px] font-semibold">Refund desk</span>
          <span className="ml-auto rounded-full bg-success/11 px-2.5 py-0.5 text-[11px] font-semibold text-success-text">Live</span>
        </div>
      </Skel>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[13rem_1fr]">
        <div className="flex flex-col gap-2.5">
          {['Customer email', 'Amount', 'Reason'].map((f, i) => (
            <Skel key={f} ready={s >= 2 + i}>
              <div className="text-[11px] text-muted-foreground">{f}</div>
              <div className="glass-raised mt-1 h-8 rounded-ctl" />
            </Skel>
          ))}
          <Skel ready={s >= 5}>
            <div className="brand-fill mt-1 flex h-8 items-center justify-center rounded-full text-[12.5px] font-semibold">Send request</div>
          </Skel>
        </div>
        <div className="flex flex-col gap-2">
          <Skel ready={s >= 6}>
            <div className="grid grid-cols-[1fr_4rem_5rem] px-2 text-[11px] text-muted-foreground">
              <span>Customer</span>
              <span>Amount</span>
              <span>Route</span>
            </div>
          </Skel>
          {ROWS.map((r, i) => (
            <Skel key={r[0]} ready={s >= 7 + i}>
              <div className="glass-raised grid grid-cols-[1fr_4rem_5rem] items-center rounded-ctl px-2 py-2 text-[12px]">
                <span className="truncate">{r[0]}</span>
                <span className="font-mono">{r[1]}</span>
                <span className={r[2] === 'Manager' ? 'text-warning-text' : 'text-success-text'}>{r[2]}</span>
              </div>
            </Skel>
          ))}
          <Skel ready={s >= 10}>
            <div className="flex h-16 items-end gap-1 px-2">
              {[5, 8, 6, 11, 9, 14, 12, 16].map((v, i) => (
                <span key={i} className="flex-1 rounded-kbd bg-primary/70" style={{ height: `${v * 6}%` }} />
              ))}
            </div>
          </Skel>
        </div>
      </div>
    </div>
  )
}

/* ---------- the views, by key ---------- */

const VIEWS: Record<string, typeof CodeGen> = { code: CodeGen, ui: UIGen }

/** One generated view by key, for the hero panel's pages; always running. */
export function ShowcaseView({ which, still }: { which: string; still: boolean }) {
  const View = VIEWS[which]
  return View ? <View on still={still} /> : null
}
