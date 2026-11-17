import type { Params } from '@/features/training-gym/params'

export function StatusBar({ params }: { params: Params }) {
  return (
    <footer className="statusbar">
      <span className="ok">● gym-default</span><span>6 / 6 nodes</span><span className="sb-sep" />
      <span className="live"><i className="live-dot" style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />run_8a10 training · fold 3 of 5</span>
      <span>1 queued</span><span className="sb-sep" />
      <span>C=<b>{Number.isInteger(params.C) ? params.C.toFixed(1) : params.C}</b> · class_weight=<b>{params.classWeight === 'None' ? 'None' : "'balanced'"}</b></span>
      <span className="spacer" />
      <span>Draft saved 12s ago</span><span className="sb-sep" /><span>us-east-1</span><span className="sb-sep" /><span>v3.0.0-rc1</span><span className="sb-sep" /><span>ENSEMBLE 2.6.1</span>
    </footer>
  )
}
