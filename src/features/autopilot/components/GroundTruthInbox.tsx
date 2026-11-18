import { useEffect, useRef } from 'react'
import { StatusPill } from '@/shared/primitives/StatusPill'
import type { Step } from '../run'

/** The only place Vela waits for you. `step` is the question it is blocked on, if any. */
export function GroundTruthInbox({ step: last, blocked, finished, asked, onAnswer }: { step?: Step; blocked: boolean; finished: boolean; asked: number; onAnswer: (id: string, option: string) => void }) {
  const inboxRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (blocked) inboxRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [blocked])
  return (
  <div ref={inboxRef} className={`card inbox ${blocked ? 'open' : ''}`}>
    <div className="card-head"><div className="left"><h3 className="card-title">Ground-Truth Inbox</h3></div>{blocked ? <StatusPill status="warning">1 Needs You</StatusPill> : <StatusPill status="neutral">Empty</StatusPill>}</div>
    {blocked ? (
      <div className="card-body stack" style={{ gap: 12 }}>
        <p className="helper" style={{ color: 'var(--text-primary)' }}>{last!.finding}</p>
        {last!.evidence && (
          <div className="table-wrap well"><table className="table">
            <thead><tr><th>Text</th><th>Label</th><th>Model</th><th className="r">Conf.</th></tr></thead>
            <tbody>{last!.evidence.map((e, i) => <tr key={i}><td style={{ whiteSpace: 'normal', lineHeight: '17px', padding: '6px 12px' }}>{e.text}</td><td>{e.given}</td><td>{e.says}</td><td className="r table-cell">{e.conf.toFixed(2)}</td></tr>)}</tbody>
          </table></div>
        )}
        <div className="row" style={{ flexWrap: 'wrap' }}>
          {last!.options!.map((o, i) => <button key={o.id} className={`btn ${i === 0 ? 'primary' : ''}`} onClick={() => onAnswer(last!.id, o.id)}>{o.label}</button>)}
        </div>
        <span className="helper" style={{ fontSize: 12 }}>Vela's suggestion is first. Training is paused until you answer.</span>
      </div>
    ) : (
      <div className="card-body"><p className="helper">{finished ? `Vela asked you ${asked} time${asked === 1 ? '' : 's'} in this run.` : 'Nothing needs you. Vela stops here only when the labels themselves are in question.'}</p></div>
    )}
  </div>
  )
}
