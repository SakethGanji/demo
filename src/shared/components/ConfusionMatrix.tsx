import { useState } from 'react'

/** Confusion matrix on the sequential heat ramp (share of the actual row). */
export function ConfusionMatrix({ labels, m }: { labels: string[]; m: number[][] }) {
  const [hover, setHover] = useState<{ r: number; c: number } | null>(null)
  const heat = (p: number) => (p >= 0.5 ? 'var(--heat-5)' : p >= 0.1 ? 'var(--heat-4)' : p >= 0.03 ? 'var(--heat-3)' : p >= 0.01 ? 'var(--heat-2)' : 'var(--heat-1)')
  return (
    <div style={{ position: 'relative' }}>
      <div className="cm" style={{ gridTemplateColumns: `86px repeat(${labels.length}, 1fr)` }}>
        <div className="lab" style={{ fontFamily: 'var(--font-math)', fontStyle: 'italic', fontSize: 14 }}>y \ ŷ</div>
        {labels.map((l) => <div key={l} className="lab top">{l}</div>)}
        {m.map((row, r) => {
          const tot = row.reduce((a, b) => a + b, 0)
          return [
            <div key={`l${r}`} className="lab">{labels[r]}</div>,
            ...row.map((v, c) => {
              const p = v / tot
              return (
                <div key={`${r}-${c}`} className={`cell ${p >= 0.5 ? 'dark' : ''}`} style={{ background: heat(p) }}
                  onPointerEnter={() => setHover({ r, c })} onPointerLeave={() => setHover(null)}>{v}</div>
              )
            }),
          ]
        })}
      </div>
      {hover && (
        <div className="tooltip" style={{ right: 0, top: -36 }}>
          <span className="t-muted">y=</span>{labels[hover.r]} <span className="t-muted">ŷ=</span>{labels[hover.c]} · {m[hover.r][hover.c]} · {((m[hover.r][hover.c] / m[hover.r].reduce((a, b) => a + b, 0)) * 100).toFixed(1)}%
        </div>
      )}
    </div>
  )
}
