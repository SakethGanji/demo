import { Icon } from '@/shared/primitives/Icon'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { TITLES, type Route } from '../routes'

export function ContextBar({ route, velaOpen, onPalette, onVela }: { route: Route; velaOpen: boolean; onPalette: () => void; onVela: () => void }) {
  return (
    <header className="context-bar">
      <div className="crumbs">
        <span>ENSEMBLE</span><span className="sep">/</span>
        <span>{TITLES[route]}</span>
        {route === 'gym' && <><span className="sep">/</span><b>Enterprise Support Ticket Classifier</b></>}
      </div>
      <button className="cmd" onClick={onPalette}><Icon.search width={14} height={14} /> Search runs, models, datasets<kbd>⌘K</kbd></button>
      <span className="cluster" title="Attached compute"><span className="g">●</span>gym-default · cpu-8x</span>
      <button className={`btn vela ${velaOpen ? 'on' : ''}`} onClick={onVela}><VelaCrest size={16} /> Ask Vela</button>
    </header>
  )
}
