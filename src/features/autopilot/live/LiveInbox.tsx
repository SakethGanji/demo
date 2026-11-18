import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ApInbox, ApInboxItem, Autopilot } from '@/shared/api/types'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { MathText } from '@/shared/components/MathText'

const KINDS: { id: string; label: string }[] = [{ id: 'all', label: 'All' }, { id: 'label', label: 'Label issues' }, { id: 'conflict', label: 'Conflicts' }, { id: 'duplicate', label: 'Duplicates' }, { id: 'outlier', label: 'Outliers' }]
const PAGE = 40
const pct = (v: number) => `${(v * 100).toFixed(1)}%`

/** The Ground-Truth Inbox: the rows cleanlab doubts, ranked; you say what is true. */
export function LiveInbox({ a, onChange }: { a: Autopilot; onChange: () => void }) {
  const [ib, setIb] = useState<ApInbox | null>(null)
  const [kind, setKind] = useState('all')
  const [page, setPage] = useState(0)
  const [c, setC] = useState(0.9)
  const [sel, setSel] = useState<ApInboxItem | null>(null)
  const [busy, setBusy] = useState(false)
  const editable = a.status === 'awaiting_inbox' && !a.inbox?.submitted
  const classes = Object.keys(a.facts?.class_shares ?? {})

  const load = () => api.autopilot.inbox(a.id).then((x) => { setIb(x); setSel((s) => (s ? x.items.find((i) => i.id === s.id) ?? s : x.items[0] ?? null)) }).catch(() => {})
  useEffect(() => { load() }, [a.id, a.inbox?.applied, a.inbox?.submitted])

  const items = useMemo(() => (ib?.items ?? []).filter((i) => kind === 'all' || i.kind === kind), [ib, kind])
  const shown = items.slice(page * PAGE, (page + 1) * PAGE)
  const above = (ib?.items ?? []).filter((i) => !i.answer && i.confidence >= c).length
  const answered = (ib?.items ?? []).filter((i) => i.answer).length
  const r = a.inbox?.recall

  const answer = (it: ApInboxItem, ans: ApInboxItem['answer'], label?: string) => {
    if (!editable) return
    setIb((x) => x && { ...x, items: x.items.map((i) => (i.id === it.id ? { ...i, answer: ans, answer_label: label ?? (ans === 'relabel' ? i.suggested ?? undefined : undefined), answered_by: 'you' } : i)) })
    api.autopilot.answer(a.id, [{ id: it.id, answer: ans, label }]).catch(() => load())
  }
  const bulk = () => { setBusy(true); api.autopilot.bulk(a.id, c).then(load).finally(() => setBusy(false)) }
  const submit = () => { setBusy(true); api.autopilot.resume(a.id).then(() => { onChange(); load() }).finally(() => setBusy(false)) }

  return (
    <div className={`card inbox ${editable ? 'open' : ''}`} data-testid="ap-inbox">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Ground-Truth Inbox</h3>
          <span className="helper" style={{ fontSize: 12 }}>{a.inbox?.n_items} rows cleanlab doubts, from the baseline's out-of-sample p̂ ({a.inbox?.model})</span></div>
        {editable ? <StatusPill status="warning">{(a.inbox?.n_items ?? 0) - answered} Need You</StatusPill>
          : a.inbox?.applied ? <StatusPill status="success">Applied</StatusPill> : <StatusPill status="neutral">Submitted</StatusPill>}
      </div>

      <div className="inbox-top">
        <div className="formula compact">
          <div className="eq"><i>m</i>(<i>x</i>) = <i>p̂</i><sub><i>y</i></sub>(<i>x</i>) − max<sub><i>k</i>≠<i>y</i></sub> <i>p̂</i><sub><i>k</i></sub>(<i>x</i>)
            {sel?.margin != null && <span className="mono sub-eq"> = {sel.p_given!.toFixed(3)} − {(sel.p_given! - sel.margin).toFixed(3)} = <b>{sel.margin >= 0 ? '+' : ''}{sel.margin.toFixed(3)}</b></span>}</div>
          <dl className="where">
            <dt>x</dt><dd>a row{sel ? <> (row <b className="mono">{sel.row}</b>, {sel.split})</> : null}</dd>
            <dt>y</dt><dd>its given label{sel ? <> = <b>{sel.given}</b></> : null}</dd>
            <dt>p̂<sub>k</sub>(x)</dt><dd>the baseline's probability of class k: out-of-fold for training rows (5-fold bagging), the fitted model's for holdout rows; never a model that saw the row</dd>
            <dt>m &lt; 0</dt><dd>the model prefers another class; cleanlab's <span className="mono">find_label_issues</span> (confident learning) flags the row, ranked by m</dd>
            <dt>c</dt><dd>confidence of the suggestion: p̂ of the suggested class (relabel), 0.99 for an exact duplicate</dd>
          </dl>
        </div>
        {r && (
          <div className="recall-box" data-testid="ap-recall">
            <span className="micro-label">Planted errors · {r.source}</span>
            <div className="recall-big"><b>{pct(r.recall)}</b><span>recall</span></div>
            <div className="mono" style={{ fontSize: 12 }}>{r.found} / {r.planted} flipped labels found in the {r.top_k} label items</div>
            <div className="recall-bar"><i style={{ width: pct(r.recall) }} /><i className="p" style={{ left: pct(r.recall_at_planted), }} title="recall in the top N = planted" /></div>
            <dl className="where tight">
              <dt>precision</dt><dd>{pct(r.precision)} of label items are planted flips (the rest: genuine ambiguity in the text)</dd>
              <dt>recall@{r.planted}</dt><dd>{pct(r.recall_at_planted)} in the top {r.planted} by m(x)</dd>
              <dt>suggested</dt><dd>{pct(r.suggestion_accuracy)} of found flips get the true label as the suggestion</dd>
              {a.inbox?.applied && <><dt>fixed</dt><dd>{r.fixed} flips relabelled to their true label</dd></>}
            </dl>
          </div>
        )}
      </div>

      <div className="inbox-bar">
        <div className="seg">{KINDS.map((k) => <button key={k.id} className={kind === k.id ? 'on' : ''} onClick={() => { setKind(k.id); setPage(0) }}>{k.label}{k.id !== 'all' ? ` · ${(a.inbox?.kinds ?? {})[k.id] ?? 0}` : ''}</button>)}</div>
        <span className="spacer" />
        {editable && <>
          <span className="mono" style={{ fontSize: 12 }}>accept suggestions with c ≥</span>
          <input type="range" min={0.5} max={1} step={0.01} value={c} onChange={(e) => setC(Number(e.target.value))} aria-label="Bulk-accept confidence" />
          <b className="mono" style={{ width: 36 }}>{c.toFixed(2)}</b>
          <button className="btn sm" disabled={busy || !above} onClick={bulk} data-testid="ap-bulk">Accept {above}</button>
          <button className="btn sm primary" disabled={busy} onClick={submit} data-testid="ap-submit">Apply {answered} Answers &amp; Retrain</button>
        </>}
      </div>

      <div className="table-wrap inbox-scroll">
        <table className="table inbox-table">
          <thead><tr><th>Row</th><th>Issue</th><th>Text / features</th><th>Given</th><th>Suggested</th><th className="r">c</th><th className="r math-th"><i>m</i>(<i>x</i>)</th><th>Why</th><th>Answer</th></tr></thead>
          <tbody>
            {shown.map((it) => (
              <tr key={it.id} className={sel?.id === it.id ? 'sel' : ''} onClick={() => setSel(it)} data-testid="ap-item">
                <td className="mono">{it.row}<div className="muted" style={{ fontSize: 10.5 }}>{it.split}</div></td>
                <td><span className={`chip k-${it.kind}`}>{it.kind}</span></td>
                <td className="txt">{it.text ?? Object.entries(it.features).map(([k, v]) => `${k}=${v}`).join(' · ')}
                  {it.text && <div className="muted" style={{ fontSize: 11 }}>{Object.entries(it.features).map(([k, v]) => `${k}=${v}`).join(' · ')}</div>}</td>
                <td>{it.given}</td>
                <td>{it.suggestion === 'relabel' ? <b>{it.suggested}</b> : <span className="muted">{it.suggestion}</span>}</td>
                <td className="r mono">{it.confidence.toFixed(2)}</td>
                <td className="r mono">{it.margin != null ? it.margin.toFixed(3) : '—'}</td>
                <td className="why"><MathText text={it.why} /></td>
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="ans">
                    {(['confirm', 'relabel', 'drop', 'skip'] as const).map((x) => (
                      <button key={x} className={`ab ${it.answer === x ? 'on' : ''}`} disabled={!editable} onClick={() => answer(it, x, x === 'relabel' ? it.suggested ?? classes.find((k) => k !== it.given) : undefined)}>{x}</button>
                    ))}
                    {it.answer === 'relabel' && (
                      <select className="select sm" value={it.answer_label ?? ''} disabled={!editable} onChange={(e) => answer(it, 'relabel', e.target.value)}>
                        {classes.filter((k) => k !== it.given).map((k) => <option key={k}>{k}</option>)}
                      </select>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inbox-bar" style={{ borderTop: '1px solid var(--border-default)' }}>
        <span className="helper" style={{ fontSize: 12 }}>{items.length} rows · {answered} answered{a.inbox?.answers ? ` (${Object.entries(a.inbox.answers).map(([k, v]) => `${k} ${v}`).join(', ')})` : ''} · open rows count as skipped</span>
        <span className="spacer" />
        <button className="btn sm ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>←</button>
        <span className="mono" style={{ fontSize: 12 }}>{page + 1} / {Math.max(1, Math.ceil(items.length / PAGE))}</span>
        <button className="btn sm ghost" disabled={(page + 1) * PAGE >= items.length} onClick={() => setPage(page + 1)}>→</button>
      </div>
    </div>
  )
}
