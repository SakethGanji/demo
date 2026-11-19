/** A requirement as a fret track: the tick is your target, the node is where the model is now. */
export function Gauge({ label, sym, value, target, min, max, lower, fmt }: { label: string; sym: string; value: number; target: number; min: number; max: number; lower?: boolean; fmt: (v: number) => string }) {
  const t = (v: number) => Math.min(1, Math.max(0, (v - min) / (max - min))) * 100
  const has = value > 0
  const met = has && (lower ? value <= target : value >= target)
  return (
    <div className="gauge">
      <div className="gauge-top"><span>{label}</span><b>{has ? fmt(value) : '—'}</b></div>
      <div className="gauge-track">
        <i className="fill" style={{ width: has ? `${t(value)}%` : 0, background: met ? '#2DD4BF' : '#38BDF8' }} />
        {Array.from({ length: 11 }, (_, i) => <i key={i} className="fret" style={{ left: `${i * 10}%` }} />)}
        <i className="target" style={{ left: `${t(target)}%` }} />
        {has && <i className="node" style={{ left: `${t(value)}%`, borderColor: met ? '#2DD4BF' : '#38BDF8' }} />}
      </div>
      <div className="gauge-bot"><span className="ps-note" style={{ fontFamily: 'var(--font-math)', fontSize: 13 }}><i>{sym}</i> {lower ? '≤' : '≥'} {fmt(target)}</span><span className={met ? 'g-met' : 'g-open'}>{!has ? 'no model yet' : met ? '● met' : '○ not yet'}</span></div>
    </div>
  )
}
