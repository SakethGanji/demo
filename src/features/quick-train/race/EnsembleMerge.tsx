import { useEffect, useId, useState } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { reducedMotion, usePrefs } from '@/shared/lib/prefs'
import { sub } from '../wizard'

const ease = (t: number) => 1 - Math.pow(1 - t, 3)

/**
 * The weighted ensemble forming: one string per base model fₘ, its thickness ∝ wₘ, bending from parallel into a
 * single string ŷ. Models with wₘ = 0 stay apart, dashed. `weights` are sorted, largest first.
 */
export function EnsembleMerge({ weights, others, name }: { weights: [string, number][]; others: string[]; name: string }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const { prefs } = usePrefs()
  const [p, setP] = useState(0)
  const gid = `ens-${useId().replace(/:/g, '')}`
  const key = weights.map(([m, v]) => `${m}:${v.toFixed(4)}`).join('|')
  useEffect(() => {
    if (prefs.motion === 'Still' || reducedMotion()) { setP(1); return }
    setP(0)
    const t0 = performance.now()
    let raf = 0
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / 1600)
      setP(ease(t))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [key, prefs.motion])

  const rows = [...weights, ...others.map((m) => [m, 0] as [string, number])]
  const rowH = 30
  const H = Math.max(rows.length * rowH + 16, 90)
  const labelW = 170
  const nodeX = Math.max(labelW + 120, w - 150)
  const nodeY = H / 2
  const startX = labelW + 6
  return (
    <div ref={ref} className="ens-merge" data-testid="ensemble-merge">
      <svg width={w} height={H} style={{ display: 'block', overflow: 'visible' }} role="img" aria-label={`${name}: ${weights.map(([m, v]) => `${m} ${v.toFixed(3)}`).join(', ')}`}>
        <defs><linearGradient id={gid} gradientUnits="userSpaceOnUse" x1={startX} x2={w} y1={0} y2={0}><stop offset="0" stopColor="#2DD4BF" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        {rows.map(([m, wt], i) => {
          const y = 8 + i * rowH + rowH / 2
          const zero = wt <= 0
          const ty = zero ? y : y + (nodeY - y) * p
          const c1 = startX + (nodeX - startX) * 0.45, c2 = startX + (nodeX - startX) * 0.7
          const d = zero ? `M${startX},${y} L${startX + (nodeX - startX) * 0.35},${y}` : `M${startX},${y} C${c1},${y} ${c2},${ty} ${nodeX},${ty}`
          return (
            <g key={m}>
              <text x={labelW - 4} y={y + 4} textAnchor="end" className="ens-lab" opacity={zero ? 0.45 : 1}>
                {zero ? m : <><tspan className="ens-f">f{sub(i + 1)}</tspan>{'  '}{m}</>}
              </text>
              <circle cx={startX} cy={y} r={2.5} fill={zero ? '#475569' : '#2DD4BF'} />
              <path d={d} fill="none" stroke={zero ? '#475569' : `url(#${gid})`} strokeWidth={zero ? 1 : 1 + 7 * wt} strokeDasharray={zero ? '3 5' : undefined} strokeLinecap="round" opacity={zero ? 0.6 : 0.55 + 0.45 * wt} />
              {!zero && <text x={startX + 10} y={y - 6} className="ens-w">w{sub(i + 1)} = {wt.toFixed(3)}</text>}
            </g>
          )
        })}
        <line x1={nodeX} x2={nodeX + (w - nodeX - 40) * p} y1={nodeY} y2={nodeY} stroke={`url(#${gid})`} strokeWidth={3} strokeLinecap="round" />
        <circle cx={nodeX} cy={nodeY} r={6} fill="#0A1120" stroke="#2DD4BF" strokeWidth={2} />
        <text x={nodeX + (w - nodeX - 40) * p + 8} y={nodeY + 5} className="ens-out" opacity={p}>ŷ</text>
        <text x={nodeX} y={nodeY + 22} textAnchor="middle" className="ens-w" opacity={p}>Σ wₘ = 1</text>
      </svg>
    </div>
  )
}
