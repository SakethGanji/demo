/** Pretty JSON with token colours; wraps (`pre-wrap`) and scrolls vertically inside itself (§4.7). */
import { cn } from '@/shared/lib/utils'

const TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g

export function jsonText(v: unknown): string {
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2) ?? 'null'
  } catch {
    return String(v)
  }
}

export function JsonBlock({ value, className, testId }: { value: unknown; className?: string; testId?: string }) {
  const text = jsonText(value)
  const parts: React.ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  TOKEN.lastIndex = 0
  let i = 0
  while ((m = TOKEN.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    if (m[1] && m[2]) {
      parts.push(
        <span key={i++} style={{ color: 'var(--syn-fn)' }}>
          {m[1]}
        </span>,
        m[2],
      )
    } else if (m[1]) parts.push(<span key={i++} style={{ color: 'var(--syn-string)' }}>{m[1]}</span>)
    else if (m[3]) parts.push(<span key={i++} style={{ color: 'var(--syn-keyword)' }}>{m[3]}</span>)
    else if (m[4]) parts.push(<span key={i++} style={{ color: 'var(--syn-number)' }}>{m[4]}</span>)
    last = TOKEN.lastIndex
  }
  if (last < text.length) parts.push(text.slice(last))
  return (
    <pre
      data-testid={testId}
      className={cn(
        'well max-h-72 overflow-y-auto p-3 font-mono text-[12px] leading-[18px] break-words whitespace-pre-wrap',
        className,
      )}
    >
      {parts}
    </pre>
  )
}
