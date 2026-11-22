import { useState } from 'react'
import { DEFAULT_PREFS, usePrefs } from '@/shared/lib/prefs'
import { PluckString } from '@/shared/primitives/PluckString'
import { SettingRow } from './components/SettingRow'
import { LlmCard } from './components/LlmCard'
import { TeamsSection } from './components/TeamsSection'
import { useApi } from '@/shared/api/mode'

export function SettingsPage() {
  const { set } = usePrefs()
  const { mode } = useApi()
  const [pluck, setPluck] = useState(0)
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
            <SettingRow k="tension" name="String Tension" help="How far a plucked string swings and how long it rings. Loose is slow and wide; Tight is quick and small." options={['Loose', 'Taut', 'Tight']} />
            <div className="set-try" onPointerEnter={() => setPluck((n) => n + 1)} onClick={() => setPluck((n) => n + 1)}>
              <span className="micro-label">Try It · hover or click</span>
              <PluckString pluck={pluck} height={30} at={0.25} />
            </div>
            <SettingRow k="layout" name="Layout" help="Inset floats the workspace as a rounded panel inside the navy frame. Flush runs it edge to edge." options={['Inset', 'Flush']} />
            <SettingRow k="motion" name="String Motion" help="Still freezes every wave and string. Your system's reduced-motion setting always wins." options={['Live', 'Still']} />
            <SettingRow k="density" name="Density" help="Row height and spacing in tables and panels." options={['Compact', 'Comfortable']} />
            <SettingRow k="mode" name="Default Mode" help="Which side of the Guided | Advanced switch configuration surfaces open on." options={['Guided', 'Advanced']} />
            <SettingRow k="velaOpen" name="Vela Sidecar" help="Whether Vela is open when the workbench loads." options={['Open', 'Closed']} />
          </div>
        </div>
        {mode === 'live' && <LlmCard />}
      </div>
      {/* U2 (C §2.1): Settings › Teams: the teams you are in, members and roles, policy, legal holds */}
      <div style={{ marginTop: 'var(--space-16)' }} id="teams"><TeamsSection /></div>
    </div>
  )
}
