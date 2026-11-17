import { Graph } from '@/shared/components/Graph'
import type { Params } from '../params'

/** Pipeline on graph paper: catenary strings between ports. Solid = valid, dashed = warning. */
export function PipelineCard({ params }: { params: Params }) {
  return (
    <div className="card">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Pipeline</h3><span className="helper" style={{ fontSize: 12 }}>TF-IDF → LogReg (OvR)</span></div>
        <div className="row">
          <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--accent-primary)" strokeWidth="2" /></svg>Compatible</span>
          <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--status-warning)" strokeWidth="2" strokeDasharray="4 3" /></svg>Warning</span>
        </div>
      </div>
      <Graph width={770} height={230}
        nodes={[
          { id: 'text', x: 24, y: 24, title: 'ticket_body', sub: 'Text column', sig: 'str[ ]' },
          { id: 'meta', x: 24, y: 134, title: 'priority, channel', sub: 'Metadata', sig: 'cat[ ]', warn: '3.1% nulls in priority' },
          { id: 'tfidf', x: 290, y: 24, title: 'TF-IDF', sub: `ngram=(1,${params.ngram}) · min_df=${params.minDf}`, sig: 'str[ ] → sparse ℝⁿˣᵈ' },
          { id: 'onehot', x: 290, y: 134, title: 'OneHotEncoder', sub: 'handle_unknown=ignore', sig: 'cat[ ] → ℝⁿˣ¹²' },
          { id: 'model', x: 556, y: 79, title: 'LogReg (OvR)', sub: 'Step 7 · editing', sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣ⁶', active: true },
        ]}
        edges={[
          { from: 'text', to: 'tfidf' }, { from: 'meta', to: 'onehot', kind: 'warn' },
          { from: 'tfidf', to: 'model', kind: 'active' }, { from: 'onehot', to: 'model' },
        ]} />
    </div>
  )
}
