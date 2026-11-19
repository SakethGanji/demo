import type { ReactNode } from 'react'
import { useApi } from '@/shared/api/mode'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

const page = (inline: boolean | undefined, card: ReactNode) => (inline ? <>{card}</> : <div className="page">{card}</div>)

/** Loading card: a whole page, or `inline` inside one. */
export const Loading = ({ children, inline }: { children: ReactNode; inline?: boolean }) => page(inline, <div className="card card-body row"><ResonanceWave /> {children}</div>)

/** Error card: a whole page, or `inline` inside one. */
export const ErrorCard = ({ err, inline }: { err: ReactNode; inline?: boolean }) => page(inline, <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>)

/** One cell of an `explain-hero`: label, mono value, optional small note. */
export const HeroCell = ({ label, sub, subClass, children }: { label: string; sub?: string; subClass?: string; children: ReactNode }) => (
  <div className="eh-cell"><span className="micro-label">{label}</span><b className="mono">{children}</b>{sub != null && <small className={subClass}>{sub}</small>}</div>
)

/** Live-only page gate: `mock` while the API is offline, a loading card while connecting, else the page. */
export function ApiOnly({ mock, children }: { mock: ReactNode; children: ReactNode }) {
  const { mode } = useApi()
  return mode === 'mock' ? <>{mock}</> : mode === 'checking' ? <Loading>Connecting…</Loading> : <>{children}</>
}
