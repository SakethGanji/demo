/**
 * The API suite strip above the cells, shown once a notebook has HTTP cells or environments: the active environment
 * (named sets of variables; secrets are referenced by key), Run suite with a pass/fail summary, Import from cURL or the
 * API catalog, Keep as test suite, and Ask shortcuts (generate negative tests). Everything calls the engine.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ViewSwitch } from '@/shared/glass'
import { errorMessage } from '@/shared/api/http'
import { useSecrets } from '@/features/project/hooks/useProjectData'
import { useApiSpec, useApiSpecs } from '@/features/api-catalog/useApiSpecs'
import { notebookApi } from '../api'
import { useNb } from '../context'
import { nbKeys } from '../hooks'
import { askNotice, plural } from '../lib/cells'
import type { Cell, EnvSet, HttpOutput, HttpSpec, ImportHttpResponse, NotebookEnvironments } from '../types'

const WRITES = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export function httpCells(cells: Cell[]): Cell[] {
  const out: Cell[] = []
  for (const c of [...cells].sort((a, b) => a.position - b.position)) {
    if (c.kind === 'http') out.push(c)
    for (const ch of c.children ?? []) if (ch.kind === 'http') out.push(ch)
  }
  return out
}

export function suiteSummary(cells: Cell[]) {
  const hs = httpCells(cells)
  let ran = 0, ok = 0, bad = 0, checks = 0, checksFailed = 0
  for (const c of hs) {
    const r = c.latest
    if (!r || c.stale) continue
    ran++
    if (r.status === 'error') bad++
    else if (r.status === 'success') ok++
    const a = ((r.output ?? {}) as HttpOutput).assertions ?? []
    checks += a.length
    checksFailed += a.filter((x) => !x.passed).length
  }
  return { total: hs.length, ran, ok, bad, checks, checksFailed }
}

export function ApiSuiteBar({ importOpen, setImportOpen, onKeep }: { importOpen: boolean; setImportOpen: (v: boolean) => void; onKeep: () => void }) {
  const { nb, actions, me, fail, live, caps, notify } = useNb()
  const envs = useQuery({ queryKey: nbKeys.envs(nb.id), queryFn: () => notebookApi.environments(nb.id), retry: false })
  const [editEnv, setEditEnv] = useState(false)
  const hs = httpCells(nb.cells)
  const sum = suiteSummary(nb.cells)
  const sets = envs.data?.sets ?? []
  if (hs.length === 0 && sets.length === 0 && !importOpen) return null
  const aiOn = !!caps?.ai.available
  const runSuite = () => {
    // a POST/PUT/PATCH/DELETE request is a write: a rerun only re-sends it when asked, and Run suite asks for all of them
    const reapply = hs.filter((c) => WRITES.has(String((c.spec as HttpSpec).method ?? 'GET'))).map((c) => c.id)
    actions.run.mutate({ triggered_by: me, reapply }, { onError: fail })
  }
  const askFor = (text: string) =>
    actions.ask.mutate({ text, by: me }, {
      onSuccess: (r) => notify(...askNotice(r)),
      onError: fail,
    })
  const verdict = sum.ran === 0 ? 'not run yet' : sum.bad === 0 && sum.ran === sum.total ? 'all passed' : `${sum.bad} failed`
  return (
    <section className="nb-suite" aria-label="API tests" data-testid="nb-suite">
      <div className="nb-row">
        <span className="nb-suite-k">⇄ API tests</span>
        {hs.length > 0 && (
          <span className="nb-suite-sum" data-testid="nb-suite-summary" data-tone={sum.ran === 0 ? undefined : sum.bad ? 'fail' : 'ok'}>
            <b>{plural(hs.length, 'request')}</b> · {verdict}
            {sum.checks > 0 && <> · {sum.checks - sum.checksFailed}/{plural(sum.checks, 'check')} pass</>}
          </span>
        )}
        <span className="nb-sp" />
        <label className="nb-suite-env">
          env
          <select
            className="nb-select"
            aria-label="Active environment"
            data-testid="nb-env-pick"
            value={envs.data?.active ?? ''}
            disabled={sets.length === 0}
            onChange={(e) => {
              const body: NotebookEnvironments = { active: e.target.value || null, sets }
              notebookApi.putEnvironments(nb.id, body).then(() => void envs.refetch(), fail)
            }}
          >
            {sets.length === 0 && <option value="">none</option>}
            {sets.length > 0 && !envs.data?.active && <option value="">none</option>}
            {sets.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
          </select>
        </label>
        <button type="button" className="nb-linkbtn text-[12.5px]" onClick={() => setEditEnv(!editEnv)} aria-expanded={editEnv} data-testid="nb-env-edit">
          {sets.length ? 'Environments' : 'Add environment'}
        </button>
        <button type="button" className="nb-linkbtn text-[12.5px]" onClick={() => setImportOpen(!importOpen)} aria-expanded={importOpen} data-testid="nb-import-open">Import</button>
      </div>
      {hs.length > 0 && (
        <div className="nb-row mt-1">
          <button type="button" className="gl-btn" data-size="sm" data-variant="primary" disabled={live || actions.run.isPending} onClick={runSuite} data-testid="nb-suite-run">
            ▶ Run suite
          </button>
          <button type="button" className="gl-btn" data-size="sm" disabled={live} onClick={onKeep} data-testid="nb-suite-keep">Keep as test suite</button>
          <span className="nb-sp" />
          <button type="button" className="gl-btn" data-size="sm" data-variant="ghost" disabled={!aiOn || live || actions.ask.isPending} title={aiOn ? 'Ask Studio for negative tests of these requests' : caps?.ai.reason || 'AI is not available'}
            onClick={() => askFor('Generate negative tests for the API requests in this notebook: missing or wrong auth, invalid input, an unknown id. Each is an HTTP cell whose assertions expect the error status.')}
            data-testid="nb-suite-negative">
            ✦ Generate negative tests
          </button>
        </div>
      )}
      {editEnv && <EnvEditor value={envs.data ?? { sets: [] }} onDone={() => { setEditEnv(false); void envs.refetch() }} />}
      {importOpen && <ImportPanel onDone={() => setImportOpen(false)} />}
    </section>
  )
}

function EnvEditor({ value, onDone }: { value: NotebookEnvironments; onDone: () => void }) {
  const { nb, pid, fail } = useNb()
  const qc = useQueryClient()
  const secrets = useSecrets(pid)
  const secretKeys = [...new Set((secrets.data ?? []).filter((x) => x.type === 'secret').map((x) => x.key))].sort()
  const [sets, setSets] = useState<EnvSet[]>(() => (value.sets.length ? value.sets : [{ name: 'dev', vars: [{ name: 'base_url', value: '' }] }]))
  const [active, setActive] = useState<string | null>(value.active ?? value.sets[0]?.name ?? 'dev')
  const [err, setErr] = useState<string | null>(null)
  const put = (i: number, s: EnvSet) => setSets(sets.map((x, j) => (j === i ? s : x)))
  const save = async () => {
    setErr(null)
    try {
      const act = sets.some((s) => s.name === active) ? active : sets[0]?.name ?? null
      await notebookApi.putEnvironments(nb.id, { active: act, sets })
      void qc.invalidateQueries({ queryKey: nbKeys.envs(nb.id) })
      onDone()
    } catch (e) {
      setErr(errorMessage(e))
      fail(e)
    }
  }
  return (
    <div className="nb-env" data-testid="nb-env-editor">
      {sets.map((s, i) => (
        <div key={i} className="nb-env-set">
          <div className="nb-row">
            <input type="radio" name="nb-env-active" checked={active === s.name} onChange={() => setActive(s.name)} aria-label={`Use ${s.name}`} />
            <input className="nb-input nb-mono" style={{ width: 120 }} value={s.name} onChange={(e) => { if (active === s.name) setActive(e.target.value); put(i, { ...s, name: e.target.value }) }} aria-label="Environment name" />
            <span className="nb-sp" />
            <button type="button" className="nb-linkbtn text-[12px]" onClick={() => setSets(sets.filter((_, j) => j !== i))}>Remove</button>
          </div>
          {s.vars.map((v, k) => {
            const secret = v.secret !== undefined && v.secret !== null
            const putV = (nv: typeof v) => put(i, { ...s, vars: s.vars.map((x, j) => (j === k ? nv : x)) })
            return (
              <div key={k} className="nb-http-pair">
                <input className="nb-input nb-mono" value={v.name} onChange={(e) => putV({ ...v, name: e.target.value })} placeholder="base_url" aria-label="Variable name" />
                <select className="nb-select" value={secret ? 'secret' : 'value'} onChange={(e) => putV(e.target.value === 'secret' ? { name: v.name, secret: secretKeys[0] ?? '' } : { name: v.name, value: '' })} aria-label="Kind">
                  <option value="value">value</option>
                  <option value="secret">secret</option>
                </select>
                {secret ? (
                  <select className="nb-select" value={v.secret ?? ''} onChange={(e) => putV({ name: v.name, secret: e.target.value })} aria-label="Secret key">
                    {secretKeys.length === 0 && <option value="">no secrets in this project</option>}
                    {secretKeys.map((x) => <option key={x} value={x}>🔒 {x}</option>)}
                    {v.secret && !secretKeys.includes(v.secret) && <option value={v.secret}>🔒 {v.secret}</option>}
                  </select>
                ) : (
                  <input className="nb-input nb-mono" value={v.value ?? ''} onChange={(e) => putV({ name: v.name, value: e.target.value })} placeholder="https://staging.example.com" aria-label={`Value of ${v.name}`} />
                )}
                <button type="button" className="gl-icon-btn" aria-label="Remove variable" onClick={() => put(i, { ...s, vars: s.vars.filter((_, j) => j !== k) })}>×</button>
              </div>
            )
          })}
          <button type="button" className="nb-linkbtn text-[12px]" onClick={() => put(i, { ...s, vars: [...s.vars, { name: '', value: '' }] })}>+ Variable</button>
        </div>
      ))}
      <div className="nb-row">
        <button type="button" className="nb-linkbtn text-[12px]" onClick={() => setSets([...sets, { name: sets.length ? 'staging' : 'dev', vars: (sets[0]?.vars ?? []).map((v) => ({ ...v })) }])}>+ Environment</button>
        <span className="nb-faint">Requests use {'{{base_url}}'}; a secret is sent as its project secret and never shown.</span>
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-size="sm" data-variant="ghost" onClick={onDone}>Cancel</button>
        <button type="button" className="gl-btn" data-size="sm" data-variant="primary" onClick={() => void save()} data-testid="nb-env-save">Save</button>
      </div>
      {err && <div className="nb-err" data-testid="nb-env-error">{err}</div>}
    </div>
  )
}

function ImportPanel({ onDone }: { onDone: () => void }) {
  const { nb, pid, me, actions } = useNb()
  const [mode, setMode] = useState<'curl' | 'catalog'>('curl')
  const [curl, setCurl] = useState('')
  const [preview, setPreview] = useState<ImportHttpResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const specs = useApiSpecs(pid)
  const [specId, setSpecId] = useState('')
  const detail = useApiSpec(pid, specId || undefined)
  const [ops, setOps] = useState<Set<string>>(new Set())
  useEffect(() => setOps(new Set()), [specId])
  const opList = useMemo(() => detail.data?.operations ?? [], [detail.data])
  const go = async (add: boolean) => {
    setErr(null)
    setBusy(true)
    try {
      const body = mode === 'curl' ? { curl, add, by: me } : { spec_id: specId, operation_ids: [...ops], add, by: me }
      const r = await notebookApi.importHttp(nb.id, body)
      if (add) {
        await actions.refresh()
        onDone()
      } else setPreview(r)
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="nb-env" data-testid="nb-import">
      <ViewSwitch size="sm" aria-label="Import from" value={mode} onChange={(m) => { setMode(m); setPreview(null) }} options={[{ value: 'curl', label: 'cURL' }, { value: 'catalog', label: 'API catalog' }]} />
      {mode === 'curl' ? (
        <textarea className="nb-textarea nb-mono" value={curl} onChange={(e) => { setCurl(e.target.value); setPreview(null) }} aria-label="cURL command" placeholder={"curl -X POST https://api.example.com/refunds -H 'Content-Type: application/json' -d '{\"order_id\": 1}'"} />
      ) : (
        <>
          <select className="nb-select" value={specId} onChange={(e) => setSpecId(e.target.value)} aria-label="API">
            <option value="">{specs.data?.length ? 'Choose an API…' : 'No APIs in the catalog yet'}</option>
            {(specs.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.name} · {x.operation_count} operations</option>)}
          </select>
          {opList.length > 0 && (
            <div className="nb-import-ops">
              {opList.map((o) => (
                <label key={o.operation_id} className="nb-rt-colpick">
                  <input type="checkbox" checked={ops.has(o.operation_id)} onChange={() => { const n = new Set(ops); if (n.has(o.operation_id)) n.delete(o.operation_id); else n.add(o.operation_id); setOps(n) }} />
                  <span className="nb-mono">{o.method.toUpperCase()} {o.path}</span>
                  <span className="nb-faint">{o.summary}</span>
                </label>
              ))}
            </div>
          )}
        </>
      )}
      {preview && (
        <div className="nb-card" data-testid="nb-import-preview">
          {preview.planned.map((p, i) => (
            <div key={i} className="nb-mono">{p.spec.method ?? 'GET'} {p.spec.url ?? p.spec.api_ref}{p.spec.assertions?.length ? ` · ${plural(p.spec.assertions.length, 'check')}` : ''}</div>
          ))}
          {preview.warnings.map((w, i) => <div key={i} className="nb-faint">⚠ {w}</div>)}
        </div>
      )}
      {err && <div className="nb-err" data-testid="nb-import-error">{err}</div>}
      <div className="nb-row">
        <span className="nb-faint">Credentials in a cURL are dropped: set them under Auth with a project secret.</span>
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-size="sm" data-variant="ghost" onClick={onDone}>Cancel</button>
        <button type="button" className="gl-btn" data-size="sm" disabled={busy || (mode === 'curl' ? !curl.trim() : !specId || ops.size === 0)} onClick={() => void go(false)}>Preview</button>
        <button type="button" className="gl-btn" data-size="sm" data-variant="primary" disabled={busy || (mode === 'curl' ? !curl.trim() : !specId || ops.size === 0)} onClick={() => void go(true)} data-testid="nb-import-add">
          Add as cells
        </button>
      </div>
    </div>
  )
}
