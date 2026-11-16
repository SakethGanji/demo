import { useState } from 'react'
import { PluckString, ToolGlyph } from '../components/Strings'
import { Segmented } from '../components/ui'
import { DEFAULT_PREFS, usePrefs, type Prefs } from '../prefs'
import { TOOLS } from '../tools'

function Row<K extends keyof Prefs>({ k, name, help, options }: { k: K; name: string; help: string; options: Prefs[K][] }) {
  const { prefs, set } = usePrefs()
  return (
    <div className="set-row">
      <div><b>{name}</b><p className="helper">{help}</p></div>
      <Segmented options={options as string[]} value={prefs[k] as string} onChange={(v) => set({ [k]: v } as Partial<Prefs>)} />
    </div>
  )
}

export function Settings() {
  const { prefs, set } = usePrefs()
  const [pluck, setPluck] = useState(0)
  const toggle = (id: string) => set({ pinned: prefs.pinned.includes(id) ? prefs.pinned.filter((p) => p !== id) : [...prefs.pinned, id] })
  return (
    <div className="page" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <div><h1 className="page-title">Settings</h1><p className="helper sub">Make the workbench yours. Changes apply at once and are kept in this browser.</p></div>
        <button className="btn" onClick={() => set(DEFAULT_PREFS)}>Reset To Defaults</button>
      </div>
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.3fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <div className="card">
          <div className="card-head"><h2 className="card-title">Workbench</h2></div>
          <div className="card-body" style={{ padding: '4px 20px' }}>
            <Row k="tension" name="String Tension" help="How far a plucked string swings and how long it rings. Loose is slow and wide; Tight is quick and small." options={['Loose', 'Taut', 'Tight']} />
            <div className="set-try" onPointerEnter={() => setPluck((n) => n + 1)} onClick={() => setPluck((n) => n + 1)}>
              <span className="micro-label">Try It · hover or click</span>
              <PluckString pluck={pluck} height={30} at={0.25} />
            </div>
            <Row k="motion" name="String Motion" help="Still freezes every wave and string. Your system's reduced-motion setting always wins." options={['Live', 'Still']} />
            <Row k="density" name="Density" help="Row height and spacing in tables and panels." options={['Compact', 'Comfortable']} />
            <Row k="mode" name="Default Mode" help="Which side of the Guided | Advanced switch configuration surfaces open on." options={['Guided', 'Advanced']} />
            <Row k="velaOpen" name="Vela Sidecar" help="Whether Vela is open when the workbench loads." options={['Open', 'Closed']} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2 className="card-title">Toolbelt</h2><span className="micro-label">{prefs.pinned.length} of {TOOLS.length} pinned</span></div>
          <div>
            {TOOLS.map((t) => {
              const on = prefs.pinned.includes(t.id)
              return (
                <button key={t.id} className={`belt-row ${on ? 'on' : ''}`} aria-pressed={on} onClick={() => toggle(t.id)}>
                  <span className="belt-glyph"><ToolGlyph id={t.id} /></span>
                  <span className="belt-text"><b>{t.name}</b><span className="micro-label">{t.verb}</span></span>
                  <span className="pin-state">{on ? '● On Belt' : '○ Pin'}</span>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
