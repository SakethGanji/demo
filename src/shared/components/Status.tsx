import type { ReactNode } from 'react'
import { useApi } from '@/shared/api/mode'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

/** Full-page loading card. */
export const Loading = ({ children }: { children: ReactNode }) => (
  <div className="page"><div className="card card-body row"><ResonanceWave /> {children}</div></div>
)

/** Full-page error card. */
export const ErrorCard = ({ err }: { err: ReactNode }) => (
  <div className="page"><div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div></div>
)

/** One cell of an `explain-hero`: label, mono value, optional small note. */
export const HeroCell = ({ label, sub, subClass, children }: { label: string; sub?: string; subClass?: string; children: ReactNode }) => (
  <div className="eh-cell"><span className="micro-label">{label}</span><b className="mono">{children}</b>{sub != null && <small className={subClass}>{sub}</small>}</div>
)

/** Live-only page gate: `mock` while the API is offline, a loading card while connecting, else the page. */
export function ApiOnly({ mock, children }: { mock: ReactNode; children: ReactNode }) {
  const { mode } = useApi()
  return mode === 'mock' ? <>{mock}</> : mode === 'checking' ? <Loading>Connecting…</Loading> : <>{children}</>
}
