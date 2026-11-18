import type { Params } from '@/features/training-gym/params'
import { useApi } from '@/shared/api/mode'

/** Live when the API answers, mock (the in-browser simulation) when it doesn't. Glyph plus word, never colour alone. */
function ApiMode() {
  const { mode, health, recheck } = useApi()
  const label = mode === 'live' ? `● live · API ${health?.version ?? ''}` : mode === 'mock' ? '▲ mock mode · API offline' : '○ connecting'
  const title = mode === 'live' ? 'Connected to the ENSEMBLE API: datasets and runs are real.'
    : mode === 'mock' ? 'The API is not reachable, so the app runs on its built-in simulation. Click to check again.' : 'Checking the API…'
  return <button type="button" className={`api-mode ${mode}`} data-testid="api-mode" data-mode={mode} title={title} onClick={recheck}>{label}</button>
}

export function StatusBar({ params }: { params: Params }) {
  return (
    <footer className="statusbar">
      <ApiMode /><span className="sb-sep" />
      <span className="ok">● gym-default</span><span>6 / 6 nodes</span><span className="sb-sep" />
      <span className="live"><i className="live-dot" style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />run_8a10 training · fold 3 of 5</span>
      <span>1 queued</span><span className="sb-sep" />
      <span>C=<b>{Number.isInteger(params.C) ? params.C.toFixed(1) : params.C}</b> · class_weight=<b>{params.classWeight === 'None' ? 'None' : "'balanced'"}</b></span>
      <span className="spacer" />
      <span>Draft saved 12s ago</span><span className="sb-sep" /><span>us-east-1</span><span className="sb-sep" /><span>v3.0.0-rc1</span><span className="sb-sep" /><span>ENSEMBLE 2.6.1</span>
    </footer>
  )
}
