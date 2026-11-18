import { useState } from 'react'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { DEFAULT_RANGE, RANGE_PRESETS, centreLevel, mainInterval, parseLevels, type Choices } from '../wizard'

const pct = (v: number) => `${Math.round(v * 1000) / 10}%`
const g = (t: number) => String(Number(t.toFixed(6)))

/**
 * F9: a numeric label can be predicted as one value ŷ or as a range: quantile regression, AutoGluon's
 * problem_type='quantile' with the levels τ chosen here. Guided: three ranges; Advanced: any levels and conformal
 * recalibration (AutoGluon's calibrate).
 */
export function RangeCard({ ch, set, label }: { ch: Choices; set: (p: Partial<Choices>) => void; label: string }) {
  const r = ch.range ?? null
  const [open, setOpen] = useState(false)
  const [text, setText] = useState((r ?? DEFAULT_RANGE).levels.join(', '))
  const parsed = parseLevels(text)
  const iv = r ? mainInterval(r.levels) : null
  const preset = r ? RANGE_PRESETS.find((p) => p.levels.join() === r.levels.join()) : null
  const use = (levels: number[]) => { set({ range: { levels, conformal: r?.conformal ?? null } }); setText(levels.join(', ')) }

  return (
    <div className="field" data-testid="range-card">
      <span className="micro-label">Predict</span>
      <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-8)' }}>
        <div className="seg" role="tablist" data-testid="predict-kind">
          <button role="tab" aria-selected={!r} className={!r ? 'on' : ''} onClick={() => set({ range: null })}>one value ŷ</button>
          <button role="tab" aria-selected={!!r} className={r ? 'on' : ''} data-testid="predict-range" onClick={() => use((r ?? DEFAULT_RANGE).levels)}>a range [q̂<sub>lo</sub>, q̂<sub>hi</sub>]</button>
        </div>
        {r && <span className="helper" style={{ fontSize: 12 }}>problem_type = <b className="mono">quantile</b> · eval_metric = <b className="mono">pinball_loss</b></span>}
      </div>
      {r && (
        <div className="stack" style={{ gap: 'var(--space-8)', marginTop: 8 }}>
          <div className="row" style={{ flexWrap: 'wrap', gap: 6 }} data-testid="range-presets">
            {RANGE_PRESETS.map((p) => (
              <button key={p.key} className={`chip-btn ${preset?.key === p.key ? 'on' : ''}`} aria-pressed={preset?.key === p.key} data-testid={`range-${p.key}`} onClick={() => use(p.levels)}>
                {p.name} <span className="mono muted">τ = {p.levels.join(' · ')}</span>
              </button>
            ))}
            {!preset && <span className="chip">custom τ = {r.levels.map(g).join(' · ')}</span>}
          </div>
          <div className="formula" data-testid="range-math">
            <div className="eq"><i>q̂</i><sub>τ</sub>(<i>x</i>) with P(<i>y</i> ≤ <i>q</i><sub>τ</sub>(<i>x</i>) | <i>x</i>) = τ, &nbsp;τ ∈ {'{'}{r.levels.map(g).join(', ')}{'}'}</div>
            {iv && <div className="eq">P(<i>q̂</i><sub>{g(iv.lo)}</sub>(<i>x</i>) ≤ <i>y</i> ≤ <i>q̂</i><sub>{g(iv.hi)}</sub>(<i>x</i>)) ≈ {g(iv.hi)} − {g(iv.lo)} = {pct(iv.nominal)}</div>}
            <div className="eq">ρ<sub>τ</sub>(<i>y</i>, <i>q</i>) = max(τ(<i>y</i> − <i>q</i>), (τ − 1)(<i>y</i> − <i>q</i>))</div>
            <dl className="where">
              <dt>τ</dt><dd>a quantile level: a share of rows that should fall at or below its prediction</dd>
              <dt>q̂<sub>τ</sub>(x)</dt><dd>the predicted τ-quantile of <b className="mono">{label}</b> for row x; the point prediction is q̂<sub>{g(centreLevel(r.levels))}</sub></dd>
              <dt>ρ<sub>τ</sub></dt><dd>the pinball loss AutoGluon minimises: a miss below the quantile costs τ, above it 1 − τ</dd>
              {iv && <><dt>{pct(iv.nominal)}</dt><dd>the nominal coverage of [q̂<sub>{g(iv.lo)}</sub>, q̂<sub>{g(iv.hi)}</sub>]; Results reports the holdout's real coverage</dd></>}
            </dl>
          </div>
          <div className="card advanced" style={{ boxShadow: 'none' }}>
            <button className="card-head adv-toggle" aria-expanded={open} data-testid="range-advanced" onClick={() => setOpen(!open)}>
              <div className="left"><h3 className="card-title" style={{ fontSize: 13 }}>{open ? '▾' : '▸'} Advanced · levels, conformal</h3></div>
              <ParamChip sym="calibrate" value={r.conformal == null ? "'auto'" : String(r.conformal)} />
            </button>
            {open && (
              <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
                <label className="field">
                  <span className="micro-label">Quantile levels <span className="nocase">τ</span> (comma-separated, 0 &lt; <span className="nocase">τ</span> &lt; 1)</span>
                  <input className="input mono" data-testid="range-levels" value={text} onChange={(e) => {
                    setText(e.target.value)
                    const v = parseLevels(e.target.value)
                    if (v.ok) set({ range: { levels: v.value, conformal: r.conformal } })
                  }} />
                  {!parsed.ok && <span className="helper" role="alert" style={{ color: 'var(--status-danger)', fontSize: 12 }}>✕ {parsed.error}</span>}
                </label>
                <div className="field">
                  <span className="micro-label">Conformal recalibration (CQR) · <span className="mono" style={{ textTransform: 'none' }}>fit(calibrate=…)</span></span>
                  <div className="seg" role="tablist" data-testid="range-conformal">
                    {([[null, 'auto'], [true, 'on'], [false, 'off']] as [boolean | null, string][]).map(([v, name]) => (
                      <button key={name} role="tab" aria-selected={r.conformal === v} className={r.conformal === v ? 'on' : ''} onClick={() => set({ range: { ...r, conformal: v } })}>{name}</button>
                    ))}
                  </div>
                  <p className="helper" style={{ fontSize: 12 }}>
                    On: AutoGluon shifts each level by a constant, q̃<sub>τ</sub>(x) = q̂<sub>τ</sub>(x) + c<sub>τ</sub>, where c<sub>τ</sub> is the conformity score of the validation rows (out-of-fold rows when bagged) at rank ⌈τ(n + 1)⌉, so the interval covers what it promises on new rows. Auto: AutoGluon does it with at least 1,000 validation rows. Either way Results shows the holdout coverage raw and calibrated.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
