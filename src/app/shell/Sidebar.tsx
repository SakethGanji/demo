import wordmark from '@/shared/assets/ensemble-wordmark-on-shell.svg'
import { useApi } from '@/shared/api/mode'
import { usePrefs } from '@/shared/lib/prefs'
import { TOOLS } from '@/shared/lib/tools'
import { Icon } from '@/shared/primitives/Icon'
import { ToolGlyph } from '@/shared/primitives/ToolGlyph'
import { NAV, NAV_PARENT, type Route } from '../routes'

export function Sidebar({ route, collapsed, go, onToggle }: { route: Route; collapsed: boolean; go: (r: string) => void; onToggle: () => void }) {
  const { prefs } = usePrefs()
  const { mode, status } = useApi()
  return (
    <nav className="sidebar" aria-label="Primary">
      <button className="lockup" onClick={() => go('home')} aria-label="Project ENSEMBLE home">
        <span className="eyebrow">PROJECT</span>
        <img className="mark" src={wordmark} alt="ENSEMBLE" height={26} />
        <span className="word">ENSEMBLE</span>
      </button>
      <div className="nav">
        {NAV.map((g, gi) => (
          <div key={gi} className="nav-group">
            {g.map((n) => {
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
        ))}
        {prefs.pinned.length > 0 && (
          <div className="nav-group belt">
            <div className="belt-label">Toolbelt</div>
            {TOOLS.filter((t) => prefs.pinned.includes(t.id)).map((t) => (
              <button key={t.id} className="nav-item belt-item" title={`${t.verb} · ${t.name}`} onClick={() => go(t.route)}>
                <span className="belt-g"><ToolGlyph id={t.id} /></span><span>{t.verb}<small>{t.name}</small></span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="sidebar-footer">
        {mode === 'live' ? <>
          <div className="avatar" aria-hidden>◎</div>
          <div className="who" data-testid="sidebar-who"><b>Local workspace</b><small>no sign-in · {status?.host ?? 'this machine'}</small></div>
        </> : <>
          <div className="avatar">ML</div>
          <div className="who"><b>Maya Lin</b><small>Lead ML Engineer (demo)</small></div>
          <span className="role-pill">Admin</span>
        </>}
        <button className="icon-btn" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={onToggle}><Icon.collapse width={16} height={16} /></button>
      </div>
    </nav>
  )
}
