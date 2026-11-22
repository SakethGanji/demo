import { useRef, useState } from 'react'
import { labels, type Layer, type Prompt, type SqlResult } from '@/shared/api/labels'
import { joins, tables } from '@/shared/api/tables'
import { useFetch } from '@/shared/hooks/useFetch'
import { Callout, CopyId, Field, Flash } from '@/shared/components/DataKit'
import { plural } from '@/shared/lib/format'
import { Busy, errText } from './parts'

type Test = SqlResult & { prompts?: { row_id: number; user: string }[]; answers?: { row_id: number; label: string; confidence: number; rationale?: string }[] }

/** LB-C (orchestrator decision 2): the LLM's input is a DuckDB query over the version's tables, so people can join and
 *  aggregate first. Query editor with the tables, their columns and saved joins beside it; a results preview with
 *  DuckDB's errors inline; a template that names the query's columns as {column} (chips insert them); "Test on 5
 *  rows" shows each rendered prompt next to the answer; then save as a prompt version. */
export function PromptEditor({ layer, root, vid, v, target, by, dest, prev, onSaved, onCancel }: { layer: Layer; root: string; vid: string; v: number; target: string; by: string; dest: string
  prev?: Prompt; onSaved: (p: Prompt) => void; onCancel: () => void }) {
  const vt = useFetch(() => tables.version(root, vid), [root, vid])
  const sj = useFetch(() => joins.list(root).catch(() => []), [root])
  const spec = (prev?.spec ?? {}) as { query?: string; template?: string }
  const [query, setQuery] = useState(spec.query ?? `SELECT __row_id, subject, body, channel, priority, customer_tier\nFROM ${layer.table_name}`)
  const [tmpl, setTmpl] = useState(spec.template && spec.query ? spec.template : 'Subject: {subject}\nMessage: {body}\nChannel: {channel} · priority {priority} · tier {customer_tier}')
  const [model, setModel] = useState(prev?.model ?? 'claude-haiku-5-5')
  const [res, setRes] = useState<SqlResult | null>(null)
  const [sqlErr, setSqlErr] = useState('')
  const [test, setTest] = useState<Test | null>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const tref = useRef<HTMLTextAreaElement>(null)
  const qcols = (res?.columns ?? []).filter((c) => c !== '__row_id')
  const used = [...new Set([...tmpl.matchAll(/\{([A-Za-z_][\w]*)\}/g)].map((m) => m[1]))]
  const unknown = res ? used.filter((c) => !qcols.includes(c)) : []
  const noRowId = !!res && !res.columns.includes('__row_id')
  const run = async () => {
    setBusy('sql'); setSqlErr(''); setTest(null)
    try { setRes(await labels.sqlPreview(layer.id, vid, query, 20)) } catch (e) { setRes(null); setSqlErr(errText(e)) } finally { setBusy('') }
  }
  const insert = (c: string) => {
    const el = tref.current; const at = el?.selectionStart ?? tmpl.length
    setTmpl(tmpl.slice(0, at) + `{${c}}` + tmpl.slice(el?.selectionEnd ?? at))
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(at + c.length + 2, at + c.length + 2) })
  }
  const testRows = async () => {
    setBusy('test'); setMsg(null)
    try { setTest(await labels.llmPreview(layer.id, { version_id: vid, query, prompt_template: tmpl, limit: 5, call_llm: dest !== 'off', destination: dest, column: target }, by)) }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy('') }
  }
  const save = async () => {
    setBusy('save'); setMsg(null)
    try {
      const p = await labels.createPrompt(layer.id, { name: prev?.name ?? `${layer.name}-prompt`, column: target, version_id: vid, query, prompt_template: tmpl, template: tmpl, context: qcols, model: dest === 'fake' ? 'fake-oracle' : model }, by)
      onSaved(p)
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy('') }
  }
  const ready = !!res && !noRowId && !unknown.length && used.length > 0
  return (
    <div className="stack" style={{ gap: 10 }} data-testid="lbl-prompt-editor">
      <Callout tone="info" title="The rule" testId="lbl-sql-rule"><span>The query must return <span className="mono">__row_id</span> of the <span className="mono">{layer.table_name}</span> table (the rows being labelled), plus the columns the prompt uses. Read-only DuckDB over v{v}'s tables; one SELECT (WITH allowed).</span></Callout>
      <div className="lbl-sql-grid">
        <div className="stack" style={{ gap: 6 }}>
          <Field label="Query"><textarea className="input mono lbl-sql-edit" rows={7} spellCheck={false} value={query} onChange={(e) => { setQuery(e.target.value); setRes(null); setTest(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run() } }} data-testid="lbl-llm-query" /></Field>
          <div className="row" style={{ gap: 6 }}><button className="btn sm" disabled={busy === 'sql' || !query.trim()} onClick={run} data-testid="lbl-llm-query-run">{busy === 'sql' ? 'Running…' : 'Run preview'}</button>
            <span className="muted" style={{ fontSize: 12 }}>⌘/Ctrl + Enter</span>
            {res && <span className="muted">{plural(res.n ?? res.rows.length, 'row')}{res.truncated ? ' (first 20)' : ''} · {plural(res.columns.length, 'column')} · <CopyId value={res.query_sha256} n={8} /></span>}</div>
          {sqlErr && <Callout tone="danger" title="DuckDB refused the query" testId="lbl-sql-err"><span className="mono" style={{ whiteSpace: 'pre-wrap' }}>{sqlErr}</span></Callout>}
          {noRowId && <Callout tone="danger" title="No __row_id in the result"><span>Select <span className="mono">{layer.table_name}.__row_id</span>: it says which row each answer labels.</span></Callout>}
        </div>
        <aside className="lbl-sql-side" data-testid="lbl-sql-tables">
          <span className="micro-label">tables of v{v}</span>
          {!vt.data ? <Busy>…</Busy> : vt.data.tables.map((t) => <details key={t.name} open={t.name === layer.table_name}><summary className="mono">{t.name} <span className="muted">· {t.n_rows.toLocaleString()} rows</span></summary>
            <div className="lbl-sql-cols">{['__row_id', ...(t.schema ?? []).map((c) => c.name).filter((c) => c !== '__row_id')].map((c) => <button key={c} type="button" className="chip-btn mono" title="insert into the query" onClick={() => setQuery(`${query}${/\s$/.test(query) ? '' : ' '}${c}`)}>{c}</button>)}</div></details>)}
          {(sj.data?.length ?? 0) > 0 && <><span className="micro-label" style={{ marginTop: 8 }}>saved joins</span>{sj.data!.map((j) => <span key={j.id} className="pt-chip">⋈ {j.name}</span>)}</>}
        </aside>
      </div>
      {res && <div className="table-wrap lbl-sql" data-testid="lbl-llm-query-rows"><table className="table"><thead><tr>{res.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{res.rows.slice(0, 8).map((r, i) => <tr key={i}>{res.columns.map((c) => <td key={c} className="mono" title={String(r[c] ?? '')}>{String(r[c] ?? '—').slice(0, 48)}</td>)}</tr>)}</tbody></table></div>}
      <Field label="Prompt template" hint="the row as the model sees it; {column} is filled from the query; the classes and guidelines are added by the server">
        <textarea ref={tref} className="input mono lbl-guide-edit" rows={5} value={tmpl} onChange={(e) => { setTmpl(e.target.value); setTest(null) }} data-testid="lbl-llm-template" /></Field>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }} data-testid="lbl-tmpl-chips"><span className="micro-label">insert</span>
        {qcols.length ? qcols.map((c) => <button key={c} type="button" className={`chip-btn mono ${used.includes(c) ? 'on' : ''}`} onClick={() => insert(c)} data-testid={`lbl-chip-${c}`}>{`{${c}}`}</button>) : <span className="muted">run the query to list its columns</span>}</div>
      {unknown.length > 0 && <Callout tone="danger" title="The template names columns the query does not return">{unknown.map((c) => `{${c}}`).join(', ')}</Callout>}
      {dest !== 'fake' && <Field label="Model" hint="a full model id, never an alias"><input className="input mono" style={{ width: 260 }} value={model} onChange={(e) => setModel(e.target.value)} /></Field>}
      <div className="row" style={{ gap: 6 }}>
        <button className="btn sm" disabled={!ready || !!busy} title={!res ? 'Run the query first' : unknown.length ? 'Fix the template first' : `Render 5 prompts${dest !== 'off' ? ` and ask the ${dest} destination` : ''}`} onClick={testRows} data-testid="lbl-llm-test5">{busy === 'test' ? 'Testing…' : 'Test on 5 rows'}</button>
        <button className="btn sm primary" disabled={!ready || !!busy} title={!res ? 'Run the query first' : 'Save the query and template as the next prompt version'} onClick={save} data-testid="lbl-llm-save">Save as v{(prev?.version ?? 0) + 1}</button>
        <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
      </div>
      <Flash msg={msg} testId="lbl-llm-editor-msg" />
      {test && <div className="lbl-test" data-testid="lbl-llm-test-rows">
        {(test.prompts ?? []).map((pr) => { const a = test.answers?.find((x) => x.row_id === pr.row_id)
          return <div key={pr.row_id} className="lbl-test-row"><div><span className="micro-label">row #{pr.row_id} · prompt</span><pre className="lbl-guidelines mono">{pr.user}</pre></div>
            <div><span className="micro-label">answer</span>{a ? <div className="lbl-answer"><b>{a.label}</b> <span className="mono muted">{Number(a.confidence).toFixed(2)}</span>{a.rationale && <span className="muted"> · {a.rationale}</span>}</div> : <span className="muted">not asked (destination off)</span>}</div></div> })}
      </div>}
    </div>
  )
}
