import { sub } from '../wizard'

/** ŷ(x) = Σ wₘ·fₘ(x) with the real weights, and a where-list naming every fₘ and wₘ. */
export function StackEquation({ model, weights, scoreLabel, testId = 'stack-equation', dark = false }: {
  model: string; weights: Record<string, number>; scoreLabel?: string; testId?: string; dark?: boolean
}) {
  const w = Object.entries(weights).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
  const sum = w.reduce((a, [, v]) => a + v, 0)
  return (
    <div className={`formula stack-eq ${dark ? 'dark' : ''}`} data-testid={testId}>
      <div className="micro-label">{model}{scoreLabel ? ` · ${scoreLabel}` : ''}</div>
      <div className="eq">
        <i>ŷ</i>(<i>x</i>) = {w.map(([m, v], i) => <span key={m} className="term">{i > 0 && ' + '}<b>{v.toFixed(3)}</b>·<i>f</i>{sub(i + 1)}(<i>x</i>)</span>)}
      </div>
      <dl className="where">
        <dt>ŷ(x)</dt><dd>the ensemble's output for a row x: class probabilities p̂ ∈ Δᴷ⁻¹ (classification) or a value in ℝ (regression)</dd>
        {w.map(([m, v], i) => <span key={m} style={{ display: 'contents' }}><dt>f{sub(i + 1)}</dt><dd><b className="mono">{m}</b> · w{sub(i + 1)} = {v.toFixed(4)}</dd></span>)}
        <dt>wₘ</dt><dd>Caruana greedy ensemble selection on validation predictions: wₘ = cₘ / Σ c, cₘ = times model m was picked; wₘ ≥ 0, Σ wₘ = {sum.toFixed(3)}</dd>
      </dl>
    </div>
  )
}
