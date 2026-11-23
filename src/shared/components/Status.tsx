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

/** What every page shows while the API is unreachable: no data is ever faked. The health check keeps retrying. */
export function ApiOffline({ inline }: { inline?: boolean }) {
  const { recheck } = useApi()
  return page(inline, (
    <div className="card card-body stack" role="alert" data-testid="api-offline">
      <b>▲ API offline</b>
      <p className="helper">The ENSEMBLE API is not reachable. Start it with <span className="mono">make dev</span>; this page connects as soon as it answers.</p>
      <div><button className="btn sm" onClick={recheck}>Check Again</button></div>
    </div>
  ))
}

/** Live-only gate: the offline notice while the API is unreachable, a loading card while connecting, else the children. */
export function ApiOnly({ children, inline }: { children: ReactNode; inline?: boolean }) {
  const { mode } = useApi()
  return mode === 'offline' ? <ApiOffline inline={inline} /> : mode === 'checking' ? <Loading inline={inline}>Connecting…</Loading> : <>{children}</>
}
