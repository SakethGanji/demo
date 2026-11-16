import { useEffect, useRef, useState } from 'react'

export type GNode = { id: string; x: number; y: number; title: string; sub?: string; sig?: string; active?: boolean; warn?: string }
export type GEdge = { from: string; to: string; kind?: 'ok' | 'warn' | 'active' | 'blocked' }

/** Pipeline / lineage on graph paper: catenary strings between port nodes. Scales to fit its container. */
export function Graph({ nodes, edges, width, height, nodeW = 190, nodeH = 72 }: { nodes: GNode[]; edges: GEdge[]; width: number; height: number; nodeW?: number; nodeH?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const el = ref.current!
    const ro = new ResizeObserver(() => setScale(Math.min(1.15, el.clientWidth / width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [width])
  const port = (id: string, side: 'in' | 'out') => {
    const n = nodes.find((n) => n.id === id)!
    return { x: side === 'out' ? n.x + nodeW : n.x, y: n.y + nodeH / 2 }
  }
  const path = (e: GEdge) => {
    const p = port(e.from, 'out'), q = port(e.to, 'in')
    const mx = (p.x + q.x) / 2, sag = 18
    return `M${p.x},${p.y} C${mx},${p.y + sag} ${mx},${q.y + sag} ${q.x},${q.y}`
  }
  const stroke = { ok: 'var(--accent-primary)', warn: 'var(--status-warning)', active: 'url(#g-active)', blocked: 'var(--status-danger)' }
  const dash = { ok: undefined, warn: '5 4', active: undefined, blocked: '2 6' }
  return (
    <div ref={ref} className="graph-paper" style={{ height: height * scale }}>
      <div style={{ position: 'absolute', left: '50%', top: 0, width, height, marginLeft: -(width * scale) / 2, transform: `scale(${scale})`, transformOrigin: '0 0' }}>
        <svg className="edges" aria-hidden>
          <defs><linearGradient id="g-active" x1="0" x2="1"><stop offset="0" stopColor="#0D9488" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
          {edges.map((e) => <path key={`${e.from}-${e.to}`} d={path(e)} stroke={stroke[e.kind ?? 'ok']} strokeDasharray={dash[e.kind ?? 'ok']} strokeWidth="2" fill="none" />)}
        </svg>
        {nodes.map((n) => {
          const out = edges.find((e) => e.from === n.id)
          return (
            <div key={n.id} className={`pnode ${n.active ? 'active' : ''}`} style={{ left: n.x, top: n.y, width: nodeW, height: nodeH }}>
              {edges.some((e) => e.to === n.id) && <span className="port in" />}
              {out && <span className={`port out ${out.kind === 'warn' ? 'warn' : ''}`} />}
              <div className="row" style={{ gap: 6 }}>
                <b className="card-title" style={{ fontSize: 13 }}>{n.title}</b>
                {n.warn && <span style={{ color: 'var(--status-warning)', fontSize: 11 }} title={n.warn}>▲</span>}
              </div>
              {n.sub && <div className="table-cell" style={{ color: 'var(--text-body)', fontSize: 11.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{n.sub}</div>}
              {n.sig && <div className="sig">{n.sig}</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
