import { useState, type ReactNode } from 'react'
import { Graph } from '../components/Graph'
import { ParamChip, SparkWave, StatusPill, type Status } from '../components/ui'

type Model = { name: string; family: string; eq: ReactNode; sig: string; chips: [string, string][]; runs: number; best: string; status: [Status, string] }
const MODELS: Model[] = [
  { name: 'Logistic Regression', family: 'Linear', eq: <><i>ŷ</i> = σ(<i>w</i><sup>⊤</sup><i>x</i> + <i>b</i>)</>, sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['C', '1.0'], ['ε', '1e-4']], runs: 42, best: '0.914', status: ['success', 'Approved'] },
  { name: 'Linear SVM', family: 'Linear', eq: <>min ½‖<i>w</i>‖² + <i>C</i> Σ max(0, 1 − <i>y</i><i>w</i><sup>⊤</sup><i>x</i>)</>, sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['C', '0.5']], runs: 18, best: '0.907', status: ['success', 'Approved'] },
  { name: 'Gradient Boosting', family: 'Trees', eq: <><i>F</i><sub><i>M</i></sub>(<i>x</i>) = Σ<sub><i>m</i></sub> <i>η</i> <i>h</i><sub><i>m</i></sub>(<i>x</i>)</>, sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['M', '600'], ['η', '0.05']], runs: 27, best: '0.902', status: ['success', 'Approved'] },
  { name: 'Random Forest', family: 'Trees', eq: <><i>ŷ</i> = 1/<i>M</i> Σ<sub><i>m</i></sub> <i>h</i><sub><i>m</i></sub>(<i>x</i>)</>, sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['M', '400']], runs: 11, best: '0.884', status: ['success', 'Approved'] },
  { name: 'Multinomial Naive Bayes', family: 'Probabilistic', eq: <><i>ŷ</i> = argmax<sub><i>k</i></sub> log <i>π</i><sub><i>k</i></sub> + Σ <i>x</i><sub><i>j</i></sub> log <i>θ</i><sub><i>kj</i></sub></>, sig: 'ℕⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['α', '1.0']], runs: 9, best: '0.871', status: ['success', 'Approved'] },
  { name: 'DistilBERT Classifier', family: 'Neural', eq: <><i>ŷ</i> = softmax(<i>W</i> <i>h</i><sub>[CLS]</sub>)</>, sig: 'str[ ] → ŷ ∈ ℝⁿˣᴷ', chips: [['η', '3e-5'], ['epochs', '4']], runs: 6, best: '0.921', status: ['warning', 'Experimental'] },
  { name: 'MLP', family: 'Neural', eq: <><i>ŷ</i> = softmax(<i>W</i>₂ ReLU(<i>W</i>₁<i>x</i>))</>, sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['η', '1e-3'], ['λ', '1e-4']], runs: 4, best: '0.896', status: ['warning', 'Experimental'] },
  { name: 'k-Nearest Neighbours', family: 'Probabilistic', eq: <><i>ŷ</i> = mode{'{'}<i>y</i><sub><i>i</i></sub> : <i>x</i><sub><i>i</i></sub> ∈ <i>N</i><sub><i>k</i></sub>(<i>x</i>){'}'}</>, sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣᴷ', chips: [['k', '15']], runs: 3, best: '0.842', status: ['neutral', 'Deprecated'] },
]
const FAMILIES = ['All', 'Linear', 'Trees', 'Probabilistic', 'Neural']

export function ModelLibrary({ go }: { go: (r: string) => void }) {
  const [fam, setFam] = useState('All')
  const list = MODELS.filter((m) => fam === 'All' || m.family === fam)
  return (
    <div className="page">
      <div className="page-head">
        <div><h1 className="page-title">Model Library</h1><p className="helper sub">{MODELS.length} model families · every symbol below is the same symbol on its Training Gym control</p></div>
        <div className="filterbar">{FAMILIES.map((f) => <button key={f} className={`filter ${f === fam ? 'on' : ''}`} onClick={() => setFam(f)}>{f}</button>)}</div>
      </div>
      <div className="lib-grid">
        {list.map((m) => (
          <div key={m.name} className="card lib-card">
            <div className="top">
              <div className="row" style={{ justifyContent: 'space-between' }}><h3 className="card-title">{m.name}</h3><StatusPill status={m.status[0]}>{m.status[1]}</StatusPill></div>
              <div className="eqn">{m.eq}</div>
              <div className="sigline">{m.sig}</div>
              <div className="row" style={{ flexWrap: 'wrap' }}>
                <span className="micro-label">Defaults</span>{m.chips.map(([s, v]) => <ParamChip key={s} sym={s} value={v} />)}
              </div>
            </div>
            <div className="bot">
              <span className="table-cell" style={{ color: 'var(--text-body)' }}><span style={{ whiteSpace: 'nowrap' }}>{m.runs} runs · F1: <span style={{ color: 'var(--text-primary)' }}>{m.best}</span></span></span>
              <button className="btn sm" onClick={() => go('gym')}>Train in Gym →</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const TRANSFORMS: [string, string, string, string, number, Status, string][] = [
  ['TfidfVectorizer', 'Text', 'str[ ] → sparse ℝⁿˣᵈ', 'ngram=(1,2)  min_df=3', 61, 'success', 'Compatible'],
  ['CountVectorizer', 'Text', 'str[ ] → sparse ℕⁿˣᵈ', 'ngram=(1,1)', 14, 'success', 'Compatible'],
  ['HashingVectorizer', 'Text', 'str[ ] → sparse ℝⁿˣ²ᵇ', 'b=18', 5, 'success', 'Compatible'],
  ['WordPieceTokenizer', 'Text', 'str[ ] → ℕⁿˣᴸ', 'L=256', 6, 'warning', 'Neural Only'],
  ['TruncatedSVD', 'Reduce', 'sparse ℝⁿˣᵈ → ℝⁿˣʳ', 'r=300', 27, 'success', 'Compatible'],
  ['OneHotEncoder', 'Categorical', 'cat[ ] → ℝⁿˣᵐ', 'handle_unknown=ignore', 48, 'success', 'Compatible'],
  ['SimpleImputer', 'Categorical', 'cat[ ] → cat[ ]', 'strategy=most_frequent', 48, 'success', 'Compatible'],
  ['StandardScaler', 'Numeric', 'ℝⁿˣᵈ → ℝⁿˣᵈ', 'with_mean=False', 22, 'danger', 'Blocked on sparse'],
]

const COMPAT_MODELS = ['LogReg', 'Linear SVM', 'GBM', 'Random Forest', 'Naive Bayes', 'DistilBERT', 'MLP', 'k-NN']
const COMPAT = ['oowwoxoo', 'oowwoxww', 'oowwxxow', 'xxxxxoxx', 'oooowxoo', 'ooooowoo', 'oooooooo', 'wwooxxoo']

export function PreprocessingLibrary() {
  return (
    <div className="page">
      <div className="page-head">
        <div><h1 className="page-title">Preprocessing Library</h1><p className="helper sub">{TRANSFORMS.length} transforms · port contracts are checked against the open project, TF-IDF → LogReg (OvR)</p></div>
        <button className="btn primary">Register Transform</button>
      </div>
      <div className="grid two">
        <div className="card table-wrap">
          <table className="table">
            <thead><tr><th>Transform</th><th>Kind</th><th>Port Contract</th><th>Defaults</th><th className="r">Used In</th><th>Compatibility</th></tr></thead>
            <tbody>
              {TRANSFORMS.map(([n, k, sig, d, u, st, word], i) => (
                <tr key={n} className={i === 0 ? 'sel' : ''}>
                  <td className="table-cell">{n}</td><td className="muted">{k}</td><td><span className="sigline">{sig}</span></td>
                  <td className="table-cell muted">{d}</td><td className="r table-cell">{u}</td><td><StatusPill status={st}>{word}</StatusPill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">TfidfVectorizer</h3><span className="chip">v1.4.2</span></div><StatusPill status="success">Compatible</StatusPill></div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div className="formula" style={{ padding: '12px 16px' }}>
              <div className="eq" style={{ fontSize: 18, padding: '4px 0 8px' }}>tfidf(<i>t</i>, <i>d</i>) = tf(<i>t</i>, <i>d</i>) · log(<i>n</i> / df(<i>t</i>))</div>
              <dl className="where"><dt>t, d</dt><dd>A term and a ticket</dd><dt>n</dt><dd>Tickets in the training fold</dd><dt>df(t)</dt><dd>Tickets containing the term</dd></dl>
            </div>
          </div>
          <Graph width={372} height={104} nodeW={104} nodeH={60}
            nodes={[{ id: 'a', x: 10, y: 22, title: 'ticket_body', sig: 'str[ ]' }, { id: 'b', x: 134, y: 22, title: 'TF-IDF', sig: 'sparse ℝⁿˣᵈ', active: true }, { id: 'c', x: 258, y: 22, title: 'LogReg', sig: 'ŷ ∈ ℝⁿˣ⁶' }]}
            edges={[{ from: 'a', to: 'b' }, { from: 'b', to: 'c', kind: 'active' }]} />
        </div>
      </div>
      <div className="card table-wrap">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Compatibility Matrix</h3><span className="micro-label">Transform Output → Model Input</span></div>
          <div className="row"><span className="legend"><span style={{ color: 'var(--status-success)' }}>●</span>Compatible</span><span className="legend"><span style={{ color: 'var(--status-warning)' }}>▲</span>Needs Adapter</span><span className="legend"><span style={{ color: 'var(--status-danger)' }}>✕</span>Blocked</span></div>
        </div>
        <table className="compat">
          <thead><tr><th>Transform</th>{COMPAT_MODELS.map((m) => <th key={m}>{m}</th>)}</tr></thead>
          <tbody>
            {TRANSFORMS.map(([n], i) => (
              <tr key={n}><td className="table-cell" style={{ color: 'var(--text-primary)' }}>{n}</td>
                {COMPAT[i].split('').map((c, j) => <td key={j} className={c === 'o' ? 'ok' : c === 'w' ? 'wn' : 'no'} title={`${n} → ${COMPAT_MODELS[j]}`}>{c === 'o' ? '●' : c === 'w' ? '▲' : '✕'}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const VERSIONS: [string, Status, string, string, string, string, string, string][] = [
  ['v3.0.0-rc1', 'warning', 'Staging', 'run_894a', '0.914', '0.941', 'Awaiting · Arun Patel', '2026-11-16'],
  ['v2.4.1', 'success', 'Production', 'run_81c2', '0.893', '0.925', 'Maya Lin', '2026-09-02'],
  ['v2.4.0', 'neutral', 'Archived', 'run_80aa', '0.889', '0.921', 'Maya Lin', '2026-08-11'],
  ['v2.3.0', 'neutral', 'Archived', 'run_7f31', '0.874', '0.910', 'Jo Kim', '2026-06-24'],
  ['v2.2.0', 'danger', 'Rejected', 'run_7e09', '0.861', '0.902', 'Arun Patel', '2026-05-30'],
]

export function Registry() {
  return (
    <div className="page">
      <div className="page-head">
        <div><h1 className="page-title">Model Registry</h1><p className="helper sub">Enterprise Support Ticket Classifier · 5 versions · 1 in Production</p></div>
        <div className="row"><button className="btn">Compare Versions</button><button className="btn primary">Approve v3.0.0-rc1</button></div>
      </div>
      <div className="grid kpis">
        {([['Production', 'v2.4.1', 'F1: 0.893 · 75d live', [0.893, 0.892, 0.893, 0.891, 0.889, 0.887, 0.884]], ['Staging', 'v3.0.0-rc1', 'Δ +0.021 vs v2.4.1', [0.9, 0.905, 0.91, 0.912, 0.914, 0.914, 0.914]], ['Drift · 7d', '0.031', 'PSI · threshold 0.10', [0.01, 0.012, 0.018, 0.02, 0.026, 0.028, 0.031]], ['Requests · 24h', '48,210', 'p95: 41 ms', [38, 41, 44, 40, 47, 49, 48]]] as [string, string, string, number[]][]).map(([l, v, f, sp]) => (
          <div key={l} className="card kpi">
            <span className="micro-label">{l}</span>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}><span className="metric-hero" style={{ fontSize: 19, whiteSpace: 'nowrap' }}>{v}</span><SparkWave data={sp} /></div>
            <div className="foot"><span className="num">{f}</span></div>
          </div>
        ))}
      </div>
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Version Lineage</h3></div>
          <div className="row">
            <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--accent-primary)" strokeWidth="2" /></svg>Approved</span>
            <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--status-warning)" strokeWidth="2" strokeDasharray="4 3" /></svg>Awaiting Approval</span>
          </div>
        </div>
        <Graph width={1080} height={190} nodeW={176} nodeH={64}
          nodes={[
            { id: 'd1', x: 20, y: 20, title: 'zendesk_tickets_q2', sub: '98,200 rows' },
            { id: 'd2', x: 20, y: 106, title: 'zendesk_tickets_q3', sub: '124,500 rows' },
            { id: 'r1', x: 240, y: 20, title: 'run_81c2', sub: 'C=0.1 · F1: 0.893' },
            { id: 'r2', x: 240, y: 106, title: 'run_894a', sub: 'C=1.0 · F1: 0.914' },
            { id: 'v1', x: 460, y: 20, title: 'v2.4.1', sub: 'Approved · Maya Lin' },
            { id: 'v2', x: 460, y: 106, title: 'v3.0.0-rc1', sub: 'Awaiting approval', active: true },
            { id: 'p', x: 680, y: 20, title: 'Production', sub: '48,210 req / 24h' },
            { id: 's', x: 680, y: 106, title: 'Staging', sub: 'shadow 10% traffic' },
            { id: 'm', x: 884, y: 63, title: 'Drift Monitor', sub: 'PSI: 0.031' },
          ]}
          edges={[{ from: 'd1', to: 'r1' }, { from: 'd2', to: 'r2' }, { from: 'r1', to: 'v1' }, { from: 'r2', to: 'v2', kind: 'active' }, { from: 'v1', to: 'p' }, { from: 'v2', to: 's', kind: 'warn' }, { from: 'p', to: 'm' }, { from: 's', to: 'm' }]} />
      </div>
      <div className="card table-wrap">
        <table className="table">
          <thead><tr><th>Version</th><th>Stage</th><th>Source Run</th><th className="r">Macro F1</th><th className="r">AUC</th><th>Approved By</th><th className="r">Registered</th></tr></thead>
          <tbody>
            {VERSIONS.map(([v, st, word, run, f1, auc, by, at], i) => (
              <tr key={v} className={i === 0 ? 'sel' : ''}>
                <td className="table-cell">{v}</td><td><StatusPill status={st}>{word}</StatusPill></td><td className="table-cell">{run}</td>
                <td className="r table-cell">{f1}</td><td className="r table-cell">{auc}</td><td className="muted">{by}</td><td className="r table-cell">{at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
