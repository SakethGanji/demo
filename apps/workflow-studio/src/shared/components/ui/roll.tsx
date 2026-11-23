/**
 * Rolling digits (DESIGN §7): when the value changes, each changed digit slides from its old value to the
 * new one, settling right to left 20ms apart. At rest (and on first paint, and under reduced motion) it is
 * plain text, so the element's text is always the value except during the ~300ms roll.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { cn } from '@/shared/lib/utils'

const DIGITS = '0123456789'
const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function Roll({
  value,
  format,
  className,
  ...props
}: {
  value: number | string
  /** formats a number (default: toLocaleString) */
  format?: (n: number) => string
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'>) {
  const text = typeof value === 'number' ? (format ? format(value) : value.toLocaleString('en-US')) : value
  const prev = useRef(text)
  const [roll, setRoll] = useState<{ from: string; to: string; run: boolean } | null>(null)

  useLayoutEffect(() => {
    if (prev.current === text) return
    const from = prev.current
    prev.current = text
    if (reduced()) return setRoll(null)
    setRoll({ from, to: text, run: false })
  }, [text])

  useEffect(() => {
    if (!roll) return
    if (!roll.run) {
      const raf = requestAnimationFrame(() => setRoll((r) => (r ? { ...r, run: true } : r)))
      return () => cancelAnimationFrame(raf)
    }
    const t = setTimeout(() => setRoll(null), 280 + roll.to.length * 20 + 60)
    return () => clearTimeout(t)
  }, [roll])

  if (!roll) {
    return (
      <span className={cn('inline-flex tabular-nums', className)} {...props}>
        {text}
      </span>
    )
  }

  const to = [...roll.to]
  const from = [...roll.from]
  return (
    <span className={cn('inline-flex tabular-nums', className)} aria-label={roll.to} {...props}>
      {to.map((ch, i) => {
        const d = DIGITS.indexOf(ch)
        const fromRight = to.length - 1 - i
        if (d < 0) return <span key={`c${fromRight}`}>{ch}</span>
        const old = DIGITS.indexOf(from[from.length - 1 - fromRight] ?? '0')
        const at = roll.run ? d : Math.max(0, old)
        return (
          <span key={`d${fromRight}`} aria-hidden className="relative inline-block h-[1.2em] overflow-hidden leading-[1.2em]">
            <span className="invisible">{ch}</span>
            <span
              className="absolute inset-x-0 top-0 flex flex-col"
              style={{
                transform: `translateY(${-at * 1.2}em)`,
                transition: roll.run ? `transform var(--d-slow) var(--ease-out) ${fromRight * 20}ms` : 'none',
              }}
            >
              {[...DIGITS].map((x) => (
                <span key={x} className="h-[1.2em] text-center">
                  {x}
                </span>
              ))}
            </span>
          </span>
        )
      })}
    </span>
  )
}
