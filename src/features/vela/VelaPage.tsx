import { useHashParam } from '@/shared/hooks/useHashParam'
import { Segmented } from '@/shared/primitives/Segmented'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { AgentChat } from './agent/AgentChat'
import { LiveAutopilot } from './autopilot/live/LiveAutopilot'

const MODES = ['Ask Vela', 'Run it end to end'] as const
type Mode = (typeof MODES)[number]

/** G10 · the Vela page: the agent chat, and Autopilot as its "run it end to end" mode (#/vela?mode=auto; the old
 *  #/autopilot links redirect here). The conversation id is in the URL (?s=), so a reload keeps it. */
export function VelaPage({ go }: { go: (r: string) => void }) {
  const auto = useHashParam('mode') === 'auto'
  const sid = useHashParam('s')
  const mode: Mode = auto ? MODES[1] : MODES[0]
  const setMode = (m: Mode) => { location.hash = m === MODES[1] ? '#/vela?mode=auto' : '#/vela' }
  const setSid = (id: string | null) => { location.hash = id ? `#/vela?s=${id}` : '#/vela' }
  return (
    <div className="wizard" data-testid="vela-page">
      <div className="stepper-bar" style={{ paddingBottom: 14 }}>
        <div className="stepper-title" style={{ marginBottom: 0 }}>
          <VelaCrest size={26} />
          <h1 className="page-title">Vela</h1>
          <span className="helper">{auto ? 'Real runs, chained: it trains, audits the labels with cleanlab, and only stops to ask you what is true.'
            : 'Ask in plain language; Vela does it through the platform\'s own API and stops for your OK before changing anything.'}</span>
          <span className="spacer" />
          <Segmented options={[...MODES]} value={mode} onChange={setMode} />
        </div>
      </div>
      {auto ? <LiveAutopilot go={go} /> : <AgentChat sid={sid} setSid={setSid} />}
    </div>
  )
}
