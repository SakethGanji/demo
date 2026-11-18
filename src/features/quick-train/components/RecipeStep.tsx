import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import type { Dataset } from '../datasets'
import { RECIPES, expectedF1, fitSeconds, mmss, type Recipe } from '../recipes'

/** Step 2: Vela's recommendation and the recipes to choose from. */
export function RecipeStep({ ds, recipeId, onPick }: { ds: Dataset; recipeId: Recipe['id']; onPick: (id: Recipe['id']) => void }) {
  const K = ds.classes.length
  return (
    <div className="page">
      <div className="patch" style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <VelaCrest size={20} />
        <p>{ds.kind === 'text'
          ? <>Your features are mostly text and <b>K = {K}</b>. A sparse linear model is the strong first string: <b>TF-IDF → LogReg (OvR)</b> usually lands within 0.01 F1 of boosted trees at a tenth of the fit time.</>
          : <>Your features are numeric and categorical with no long text. <b>XGBoost</b> handles mixed scales and interactions without scaling; start there.</>}</p>
      </div>
      <div className="lib-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
        {RECIPES.filter((r) => ds.kind === 'text' || r.id !== 'svm').map((r) => {
          const on = r.id === recipeId, rec = r.best === ds.kind && r.id !== 'svm'
          const stages = r.stages(ds)
          return (
            <button key={r.id} className={`card lib-card recipe ${on ? 'on' : ''}`} onClick={() => onPick(r.id)} aria-pressed={on}>
              <div className="top">
                <div className="row" style={{ justifyContent: 'space-between' }}><h3 className="card-title">{r.pipeline(ds)}</h3>{rec ? <StatusPill status="info">Vela Pick</StatusPill> : <span className="pin-state">{on ? '● Selected' : '○ Select'}</span>}</div>
                <svg width="100%" height="40" aria-hidden>
                  <line x1="6%" x2="94%" y1="12" y2="12" stroke={on ? 'var(--accent-primary)' : 'var(--border-track)'} strokeWidth="2" />
                  {stages.map((s, i) => <g key={s}><circle cx={`${6 + (i / (stages.length - 1)) * 88}%`} cy="12" r="4" fill={on ? 'var(--accent-primary)' : 'var(--bg-surface)'} stroke={on ? 'var(--accent-primary)' : 'var(--border-track)'} strokeWidth="1.5" /><text x={`${6 + (i / (stages.length - 1)) * 88}%`} y="34" textAnchor={i === 0 ? 'start' : i === stages.length - 1 ? 'end' : 'middle'} className="tick-text" style={{ fontFamily: 'var(--font-sans)' }}>{i % 2 === 0 ? s : ''}</text></g>)}
                </svg>
                <div className="eqn">{r.eq}</div>
                <p className="helper" style={{ textAlign: 'left' }}>{r.where}</p>
                <div className="row" style={{ flexWrap: 'wrap' }}><span className="micro-label">Defaults</span>{r.chips.filter(([s]) => ds.kind === 'text' || s !== 'ngram').map(([s, v]) => <ParamChip key={s} sym={s} value={v} />)}</div>
              </div>
              <div className="bot"><span className="table-cell" style={{ color: 'var(--text-body)' }}>est. {mmss(fitSeconds(r, ds))}</span><span className="table-cell">expected F1: {expectedF1(r, ds).toFixed(3)}</span></div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
