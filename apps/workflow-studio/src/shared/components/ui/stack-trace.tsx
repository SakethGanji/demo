/**
 * StackTrace (DESIGN §8, Runs): parses a Python traceback string into frames. Your frames come first, each
 * a link (`onOpenFrame`) with its source line; library frames are folded behind a toggle. Copy puts the
 * original traceback on the clipboard.
 */
import { useMemo, useState } from 'react'
import { ArrowUpRight, ChevronRight } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { Check, Copy } from 'lucide-react'
import { Button } from './button'
import { useCopy } from './snippet'

export interface StackFrame {
  file: string
  line: number
  fn: string
  code?: string
  /** true for the user's own code (not site-packages / stdlib / the runtime) */
  own: boolean
}

export interface ParsedTraceback {
  frames: StackFrame[]
  type: string
  message: string
}

const LIB = /(site-packages|dist-packages|\/lib\/python\d|<frozen |\/usr\/lib\/|\/runtime\/|\/studio\/_|\/sandbox\/|\/engine\/)/

/** Parses `Traceback (most recent call last): File "…", line N, in fn` blocks. Most recent frame first. */
export function parseTraceback(tb: string, isOwn: (file: string) => boolean = (f) => !LIB.test(f)): ParsedTraceback {
  const lines = tb.replace(/\r/g, '').split('\n')
  const frames: StackFrame[] = []
  const re = /^\s*File "([^"]+)", line (\d+)(?:, in (.+))?/
  for (let i = 0; i < lines.length; i++) {
    const m = re.exec(lines[i])
    if (!m) continue
    const next = lines[i + 1]
    const code = next && !re.test(next) && /^\s{4,}\S/.test(next) ? next.trim() : undefined
    frames.push({ file: m[1], line: Number(m[2]), fn: (m[3] ?? '<module>').trim(), code, own: isOwn(m[1]) })
  }
  const tail = [...lines].reverse().find((l) => l.trim() && !re.test(l) && !/^\s{4,}/.test(l) && !/^Traceback/.test(l)) ?? ''
  const em = /^([A-Za-z_][\w.]*)(?::\s?(.*))?$/.exec(tail.trim())
  return { frames: frames.reverse(), type: em?.[1] ?? 'Error', message: em?.[2] ?? tail.trim() }
}

const short = (f: string) => f.split('/').slice(-2).join('/')

export function StackTrace({
  traceback,
  onOpenFrame,
  isOwn,
  className,
  testId = 'stack-trace',
}: {
  traceback: string
  onOpenFrame?: (f: StackFrame) => void
  isOwn?: (file: string) => boolean
  className?: string
  testId?: string
}) {
  const parsed = useMemo(() => parseTraceback(traceback, isOwn), [traceback, isOwn])
  const own = parsed.frames.filter((f) => f.own)
  const lib = parsed.frames.filter((f) => !f.own)
  const [libOpen, setLibOpen] = useState(false)
  const { copied, copy } = useCopy()
  return (
    <div className={cn('flex flex-col gap-2', className)} data-testid={testId}>
      <div className="flex items-center gap-2">
        <span className="text-[12.5px] font-semibold">Stack</span>
        <span className="text-[12px] text-muted-foreground">· {parsed.frames.length} frame{parsed.frames.length === 1 ? '' : 's'}</span>
        <div className="flex-1" />
        <Button variant="outline" size="xs" onClick={() => copy(traceback, 'stack trace')} data-testid={`${testId}-copy`}>
          {copied ? <Check className="text-success" /> : <Copy />} Copy
        </Button>
      </div>
      <div className="flex flex-col text-[12px] [&>*+*]:border-t [&>*+*]:border-hairline">
        <div className="flex min-w-0 items-baseline gap-2 px-2 py-1.5">
          <span className="shrink-0 font-mono font-semibold text-destructive" data-testid={`${testId}-type`}>
            {parsed.type}
          </span>
          <span className="min-w-0 truncate font-sans text-foreground" title={parsed.message}>
            {parsed.message}
          </span>
        </div>
        {own.map((f, i) => (
          <button
            key={`o${i}`}
            type="button"
            onClick={() => onOpenFrame?.(f)}
            disabled={!onOpenFrame}
            className="group/frame flex flex-col gap-1 rounded-chip px-2 py-1.5 text-left hover:bg-raise hover:shadow-raise-sm disabled:hover:bg-transparent disabled:hover:shadow-none"
            data-testid={`${testId}-frame`}
            title={`Open ${f.file} at line ${f.line}`}
          >
            <span className="flex min-w-0 items-center gap-2">
              <b className="truncate font-mono font-medium">
                {short(f.file)}:{f.line}
              </b>
              <span className="truncate text-muted-foreground">in {f.fn}</span>
              {onOpenFrame && <ArrowUpRight className="ml-auto size-3.5 shrink-0 text-muted-foreground opacity-0 group-hover/frame:opacity-100" />}
            </span>
            {f.code && (
              <span className="flex min-w-0 gap-2 font-mono text-[11.5px]">
                <span className="w-7 shrink-0 text-right text-code-gutter">{f.line}</span>
                <code className="truncate text-foreground/90">{f.code}</code>
              </span>
            )}
          </button>
        ))}
        {lib.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setLibOpen((v) => !v)}
              aria-expanded={libOpen}
              className="flex items-center gap-1 rounded-chip px-2 py-1 text-left text-[11.5px] text-muted-foreground hover:text-foreground"
              data-testid={`${testId}-lib-toggle`}
            >
              <ChevronRight className={cn('size-3 transition-transform duration-(--d-fast)', libOpen && 'rotate-90')} />
              {lib.length} library frame{lib.length === 1 ? '' : 's'}
            </button>
            <div className={cn('grid transition-[grid-template-rows] duration-(--d-base) ease-(--ease-out)', libOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}>
              <div className="min-h-0 overflow-hidden">
                {lib.map((f, i) => (
                  <div key={`l${i}`} className="flex min-w-0 gap-2 px-2 py-0.5 pl-6 font-mono text-[11.5px] text-muted-foreground">
                    <span className="truncate" title={`${f.file}:${f.line}`}>
                      {short(f.file)}:{f.line}
                    </span>
                    <span className="shrink-0">in {f.fn}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
