/**
 * Pieces of the inline AI change flow (DESIGN §8): the step log (mapped from the generator's real phases),
 * the typed explanation, and the elapsed clock. The composer owns the state; these only draw it.
 */
import { Fragment, useEffect, useState } from 'react'
import { Check, FileSearch, FilePenLine, FlaskConical, ShieldCheck, Wrench } from 'lucide-react'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import type { GenPhase } from '../useGenerate'

export type StepState = 'pending' | 'running' | 'done'

export interface FlowStep {
  phase: GenPhase
  state: StepState
  /** seconds this step took (known once it is done) */
  secs: number | null
  run: string
  done: string
}

const ICON: Record<GenPhase, React.ReactNode> = {
  context: <FileSearch />,
  generating: <FilePenLine />,
  analysing: <ShieldCheck />,
  repairing: <Wrench />,
  testing: <FlaskConical />,
}

export function FlowLog({ steps }: { steps: FlowStep[] }) {
  return (
    <Well className="overflow-hidden py-0.5" role="list" aria-label="AI steps" data-testid="composer-steps">
      {steps.map((s, i) => (
        <div
          key={s.phase}
          role="listitem"
          data-state={s.state}
          data-testid={s.state === 'pending' ? undefined : `phase-${s.phase}`}
          className={cn('flex h-7 items-center gap-2.5 px-3 text-[12.5px]', i > 0 && 'shadow-[inset_0_1px_0_var(--hairline)]')}
        >
          <span className="grid size-3.5 shrink-0 place-items-center">
            {s.state === 'pending' && <span className="size-2 rounded-full shadow-[inset_0_0_0_1.25px_var(--faint)]" />}
            {s.state === 'running' && <StatusDot tone="running" />}
            {s.state === 'done' && <Check className="size-3.5 text-success-text" />}
          </span>
          <span className="text-muted-foreground [&_svg]:size-3.5">{ICON[s.phase]}</span>
          <span className={cn('min-w-0 flex-1 truncate', s.state === 'pending' && 'text-muted-foreground opacity-70')}>
            {s.state === 'done' ? s.done : s.run}
          </span>
          {s.state === 'done' && s.secs != null && <span className="font-mono text-[11px] text-muted-foreground tabular-nums">{s.secs.toFixed(1)}s</span>}
        </div>
      ))}
    </Well>
  )
}

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Types `text` out word by word on the client (the summary arrives whole). Plain text when `animate` is off. */
export function useTypewriter(text: string, animate: boolean): string {
  const words = text.split(/(\s+)/)
  const [n, setN] = useState(animate && !reduced() ? 0 : words.length)
  useEffect(() => {
    if (!animate || reduced()) {
      setN(words.length)
      return
    }
    setN(0)
    const t = setInterval(() => {
      setN((c) => {
        if (c >= words.length) {
          clearInterval(t)
          return c
        }
        return c + 2 // a word and its gap
      })
    }, 45)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, animate])
  return words.slice(0, n).join('')
}

/** The explanation line: `code` spans in backticks render as code. */
export function FlowSay({ text, animate }: { text: string; animate: boolean }) {
  const typed = useTypewriter(text, animate)
  if (!typed) return null
  const parts = typed.split(/(`[^`]*`?)/)
  return (
    <p className="px-2 text-[12.5px] leading-[1.55] text-foreground" data-testid="composer-say">
      {parts.map((p, i) =>
        p.startsWith('`') ? (
          <code key={i} className="font-mono text-[12px]">
            {p.replace(/`/g, '')}
          </code>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </p>
  )
}

/** Seconds since `startedAt`, ticking while `running`. */
export function useElapsed(startedAt: number | null, running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(t)
  }, [running])
  return startedAt ? Math.max(0, (now - startedAt) / 1000) : 0
}
