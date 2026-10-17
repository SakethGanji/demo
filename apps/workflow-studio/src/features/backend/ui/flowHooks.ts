import { useEffect, useState } from 'react'

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
