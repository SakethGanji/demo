import type { ReactNode } from 'react'

export type Status = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const GLYPH: Record<Status, string> = { success: '●', warning: '▲', danger: '✕', info: '', neutral: '○' }

/** Status is never colour alone: a glyph (or pulsing node) plus a word. */
export function StatusPill({ status, children }: { status: Status; children: ReactNode }) {
  return (
    <span className={`pill ${status}`}>
      {status === 'info' ? <span className="live-dot" /> : <span aria-hidden>{GLYPH[status]}</span>}
      {children}
    </span>
  )
}
