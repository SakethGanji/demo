import type { ReactNode } from 'react'
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
