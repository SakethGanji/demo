import { useHashParam } from '@/shared/hooks/useHashParam'
import { ApiOnly } from '@/shared/components/Status'
import { EmptyState } from '@/shared/components/EmptyState'
import { Segmented } from '@/shared/primitives/Segmented'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { AgentChat } from './agent/AgentChat'
import { EndToEnd } from './autopilot/EndToEnd'

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
      {auto ? <EndToEnd go={go} /> : (
        <ApiOnly mock={<div className="wizard-scroll"><div className="page"><EmptyState title="The agent needs the API" testId="agent-mock">
          It calls the workbench's own routes, so it runs only against a live API (<span className="mono">make dev</span>). "Run it end to end" shows the scripted prototype.
        </EmptyState></div></div>}>
          <AgentChat sid={sid} setSid={setSid} />
        </ApiOnly>
      )}
    </div>
  )
}
