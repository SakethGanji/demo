import { useApi } from '@/shared/api/mode'
import { Icon } from '@/shared/primitives/Icon'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { TITLES, type Route } from '../routes'

export function ContextBar({ route, velaOpen, onPalette, onVela }: { route: Route; velaOpen: boolean; onPalette: () => void; onVela: () => void }) {
  const { mode, status } = useApi()
  return (
    <header className="context-bar">
      <div className="crumbs">
        <span>ENSEMBLE</span><span className="sep">/</span>
        <span>{TITLES[route]}</span>
        {route === 'gym' && <><span className="sep">/</span><b>Enterprise Support Ticket Classifier</b></>}
      </div>
      <button className="cmd" onClick={onPalette}><Icon.search width={14} height={14} /> Search runs, models, datasets<kbd>⌘K</kbd></button>
      {mode === 'live'
        ? <span className="cluster" title="The machine the API trains on" data-testid="cluster"><span className="g">●</span>{status?.host ?? 'local'} · cpu × {status?.cpu_count ?? '…'}</span>
        : <span className="cluster" title="Simulated compute (mock mode)"><span className="g">●</span>gym-default · cpu-8x</span>}
      <button className={`btn vela ${velaOpen ? 'on' : ''}`} onClick={onVela}><VelaCrest size={16} /> Ask Vela</button>
    </header>
  )
}
