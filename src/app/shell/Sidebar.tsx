import wordmark from '@/shared/assets/ensemble-wordmark-on-shell.svg'
import { useApi } from '@/shared/api/mode'
import { Icon } from '@/shared/primitives/Icon'
import { NAV, NAV_PARENT, type Route } from '../routes'
import { TeamSwitcher } from './TeamSwitcher'

export function Sidebar({ route, collapsed, go, onToggle }: { route: Route; collapsed: boolean; go: (r: string) => void; onToggle: () => void }) {
  const { mode, status } = useApi()
  return (
    <nav className="sidebar" aria-label="Primary">
      <button className="lockup" onClick={() => go('home')} aria-label="Project ENSEMBLE home">
        <span className="eyebrow">PROJECT</span>
        <img className="mark" src={wordmark} alt="ENSEMBLE" height={26} />
        <span className="word">ENSEMBLE</span>
      </button>
      {mode === 'live' && !collapsed && <TeamSwitcher />}
      <div className="nav">
        <div className="nav-group">
          {NAV.map((n) => {
            const I = Icon[n.icon]
            const active = (NAV_PARENT[route] ?? route) === n.id
            return (
              <button key={n.id} className={`nav-item ${active ? 'active' : ''}`} title={n.label} aria-current={active ? 'page' : undefined}
                onClick={() => go(n.id)}>
                <I /><span>{n.label}</span>
              </button>
            )
          })}
        </div>
      </div>
      <div className="sidebar-footer">
        <div className="avatar" aria-hidden>◎</div>
        <div className="who" data-testid="sidebar-who"><b>Local workspace</b><small>no sign-in · {status?.host ?? 'this machine'}</small></div>
        <button className={`icon-btn ${route === 'settings' ? 'on' : ''}`} aria-label="Settings" title="Settings" data-testid="nav-settings" aria-current={route === 'settings' ? 'page' : undefined} onClick={() => go('settings')}><Icon.settings width={16} height={16} /></button>
        <button className="icon-btn" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={onToggle}><Icon.collapse width={16} height={16} /></button>
      </div>
    </nav>
  )
}
