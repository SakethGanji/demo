/**
 * `/p/$project/notebooks/$notebookId`: the notebook (Workbench v3). Outline rail · one calm column of typed cells ·
 * the contextual workspace (closed until needed). Runs are async: the page polls while the notebook holds a run.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { Menu } from '@base-ui/react/menu'
import { toast } from 'sonner'
import { CheckStrip, GlassSheet, SplitButton, ViewSwitch } from '@/shared/glass'
import { errorMessage, isApiError } from '@/shared/api/http'
import { useQuery } from '@tanstack/react-query'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useConnections } from '@/features/connections/useConnections'
import { isNotBuilt, notebookApi } from './api'
import { NbProvider, type NbCtx, type WsState } from './context'
import { nbKeys, useCapabilities, useDeleteNotebook, useNotebook, useNotebookActions } from './hooks'
import { AUTO_TABS, cellStatus, coerce, failedRows, plural, fmtTime, getPath, KIND_LABEL, numberCells, runningCellId, setPath, tabFor, type WsTab } from './lib/cells'
import { myName, readFlag, setMyName, writeFlag } from './lib/identity'
import { isProdEnv } from './lib/look'
import { Avatar } from './cells/Look'
import { CellView } from './cells/CellView'
import { Composer } from './composer/Composer'
import { KeepDrawer, TARGET_LABEL } from './keep/KeepDrawer'
import { RerunBar } from './rerun/RerunBar'
import { ApiSuiteBar } from './cells/ApiSuite'
import { TAB_LABEL, Workspace } from './workspace/Workspace'
import type { Cell, CellKind, DataSpec, KeepTarget, Notebook, StoredCellKind } from './types'
import './notebook.css'

function useMedia(q: string) {
  const [m, setM] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches)
  useEffect(() => {
    const mq = window.matchMedia(q)
    const on = () => setM(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [q])
  return m
}

const KIND_GLYPH: Record<StoredCellKind, string> = {
  query: 'Q', http: '⇄', workflow: '↳', rule: '≍', change: '±', approval: '✓', data: '▤', store: '⇩', browser: '◱',
  for_each: '↻', ask: '✦', note: '¶', person: '◔', wait: '◷',
}

const hm = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '')
const dayOf = (iso: string | null | undefined) => {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toDateString() === new Date().toDateString() ? 'today' : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

const STATUS_TEXT: Record<Notebook['status'], string> = { idle: 'Idle', running: 'Running', needs_you: 'Needs you', failed: 'Failed', done: 'Done' }

export function NotebookPage() {
  const { notebookId } = useParams({ strict: false }) as { notebookId: string }
  const q = useNotebook(notebookId)
  if (q.isError)
    return (
      <div className="nb-scroll glass-page">
        <div className="nb-wrap nb-mgr">
          <p className="nb-err" data-testid="nb-load-error">{isApiError(q.error, 'notebook_not_found') ? 'This notebook does not exist (or was deleted).' : errorMessage(q.error)}</p>
        </div>
      </div>
    )
  if (!q.data)
    return (
      <div className="nb-scroll glass-page">
        <div className="nb-wrap nb-faint">Loading notebook…</div>
      </div>
    )
  return <Loaded nb={q.data} />
}

function Loaded({ nb }: { nb: Notebook }) {
  const { pid, slug, project } = useProjectCtx()
  const [lp, setLp] = useState<'steps' | 'explorer'>('steps')
  const search = useSearch({ strict: false }) as { cell?: string }
  const navigate = useNavigate()
  const actions = useNotebookActions(nb.id)
  const capsQ = useCapabilities()
  const conns = useConnections(pid)
  const del = useDeleteNotebook(pid)
  const narrow = useMedia('(max-width: 1279px)')
  const [me, setMe] = useState(myName)
  const [ws, setWs] = useState<WsState>({ open: false, tab: 'results', cellId: null })
  const [wsHold, setWsHold] = useState(false)
  const [mru, setMru] = useState<WsTab[]>([])
  const [outline, setOutline] = useState(() => readFlag('outline', typeof window === 'undefined' || window.innerWidth >= 1200))
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState<string | null>(search.cell ?? null)
  const [draft, setDraft] = useState<{ kind: CellKind; parent: string | null } | null>(null)
  const [inside, setInside] = useState<string | null>(null)
  const [keep, setKeep] = useState<{ open: boolean; target: KeepTarget }>({ open: false, target: 'workflow' })
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [title, setTitle] = useState(nb.title)
  useEffect(() => setTitle(nb.title), [nb.title])

  const numbered = useMemo(() => numberCells(nb.cells), [nb.cells])
  const numberOf = useCallback((id: string | null | undefined) => numbered.find((x) => x.cell.id === id)?.n ?? '?', [numbered])
  const runningId = runningCellId(nb)
  const live = !!nb.active_run_id
  const caps = capsQ.data ?? null
  const capsMissing = capsQ.isError && isNotBuilt(capsQ.error)

  const notify = useCallback((msg: string, tone: 'ok' | 'need' | 'fail' = 'ok') => {
    if (tone === 'fail') toast.error(msg)
    else if (tone === 'need') toast.warning(msg)
    else toast.success(msg)
  }, [])
  const fail = useCallback(
    (e: unknown) => {
      if (isApiError(e, 'run_in_progress')) notify('A run is in progress; wait for it or stop it first.', 'need')
      else notify(errorMessage(e), 'fail')
    },
    [notify],
  )

  const openWs = useCallback(
    (tab: WsTab, cellId?: string | null) => {
      setWs({ open: true, tab, cellId: cellId ?? null })
      setWsHold(false)
      setMru((m) => [tab, ...m.filter((t) => t !== tab)])
    },
    [],
  )
  const closeWs = () => {
    setWs((w) => ({ ...w, open: false }))
    if (live) setWsHold(true)
  }

  // auto-open: on load, show the latest run's browser / table / write result; during a run, follow new results
  const seen = useRef<Set<string> | null>(null)
  const prevRun = useRef<{ id: string; status: string } | null>(null)
  useEffect(() => {
    const results = numbered.map((x) => x.cell).filter((c) => c.latest)
    if (seen.current === null) {
      seen.current = new Set(results.map((c) => c.latest!.id))
      if (!narrow) {
        const last = nb.runs[0]
        const inLast = results.filter((c) => c.latest!.run_id === last?.id)
        const pick = (t: WsTab) => [...inLast].reverse().find((c) => tabFor(c.kind) === t)
        const c = pick('browser') ?? pick('change') ?? pick('results') ?? pick('workflow')
        if (c) setWs({ open: true, tab: tabFor(c.kind)!, cellId: c.id })
      }
      return
    }
    for (const c of results) {
      const id = c.latest!.id
      if (seen.current.has(id)) continue
      seen.current.add(id)
      const t = tabFor(c.kind)
      // a run the page started (or saw) may finish between polls: follow any new result of the latest run
      if (!t || !AUTO_TABS.includes(t) || c.latest!.run_id !== nb.runs[0]?.id) continue
      setMru((m) => [t, ...m.filter((x) => x !== t)])
      if (wsHold || narrow) setWs((w) => ({ ...w, tab: t, cellId: c.id }))
      else setWs({ open: true, tab: t, cellId: c.id })
    }
  }, [numbered, nb.runs, live, wsHold, narrow])

  // a browser cell started: show its live frame while it runs (Take over lives there), not only once it has a result
  const followed = useRef<string | null>(null)
  useEffect(() => {
    if (!runningId) {
      followed.current = null
      return
    }
    if (followed.current === runningId) return
    const c = nb.cells.find((x) => x.id === runningId)
    if (!c || c.kind !== 'browser') return
    followed.current = runningId
    setMru((m) => ['browser', ...m.filter((x) => x !== 'browser')])
    if (wsHold || narrow) setWs((w) => ({ ...w, tab: 'browser', cellId: c.id }))
    else setWs({ open: true, tab: 'browser', cellId: c.id })
  }, [runningId, nb.cells, wsHold, narrow])

  // toast when a run ends or needs you
  const last = nb.runs[0]
  useEffect(() => {
    if (!last) return
    const p = prevRun.current
    prevRun.current = { id: last.id, status: last.status }
    if (!p || (p.id === last.id && p.status === last.status)) return
    if (p.id === last.id || p.status === 'running') {
      if (last.status === 'done') notify(`Run ${last.n} finished`)
      else if (last.status === 'needs_you') notify(`Run ${last.n} needs you · cell ${numberOf(last.stop?.cell_id)}`, 'need')
      else if (last.status === 'failed') notify(`Run ${last.n} failed at cell ${numberOf(last.stop?.cell_id)}`, 'fail')
      else if (last.status === 'stopped_at_write') notify(`Run ${last.n} stopped before an applied write`, 'need')
    }
    if (last.status !== 'running' && last.status !== 'waiting') setWsHold(false)
  }, [last?.id, last?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // ?cell= scrolls to the cell once
  useEffect(() => {
    if (search.cell) document.getElementById(`cell-${search.cell}`)?.scrollIntoView({ block: 'center' })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const editSpec = (cell: Cell, path: string, raw: unknown) => {
    // the read model cuts a data cell's rows to 50: patching that spec back would drop the rest
    if (cell.kind === 'data' && (cell.spec as DataSpec).rows_truncated) {
      notify(`This data cell has ${(cell.spec as DataSpec).row_count ?? 'more than 50'} rows; only the first 50 are shown, so it can't be edited here. Replace it with a new data cell.`, 'need')
      return
    }
    const prev = getPath(cell.spec, path)
    // read-model-only fields on a data spec (the engine refuses them back)
    let base = cell.spec
    if (cell.kind === 'data') {
      const { rows_truncated: _t, row_count: _n, ...rest } = cell.spec as DataSpec
      base = rest as Cell['spec']
    }
    const value = typeof raw === 'string' ? coerce(raw, prev) : raw
    actions.patchCell.mutate(
      { cellId: cell.id, body: { spec: setPath(base, path, value) as Cell['spec'] } },
      {
        onSuccess: (r) => {
          const res = r as { stale_from: string | null }
          if (res.stale_from && cell.latest) notify(`Cell ${numberOf(res.stale_from)} and the cells after it are stale · Rerun from here`, 'need')
        },
        onError: fail,
      },
    )
  }
  // A double-click on Run all must not land on the Stop that replaces it: a second start while one is pending is
  // ignored, and Stop stays disabled for a moment after a run this page started appears.
  const starting = useRef(false)
  const startedAt = useRef(0)
  const start = (body: Parameters<typeof actions.run.mutate>[0]) => {
    if (starting.current) return
    starting.current = true
    startedAt.current = Date.now()
    actions.run.mutate(body, { onError: fail, onSettled: () => { starting.current = false } })
  }
  const rerunFrom = (cellId: string) => start({ from_cell_id: cellId, triggered_by: me })
  const runAll = () => start({ triggered_by: me })
  const [stopHold, setStopHold] = useState(false)
  useEffect(() => {
    if (!live || Date.now() - startedAt.current > 10_000) return
    setStopHold(true)
    const t = setTimeout(() => setStopHold(false), 800)
    return () => clearTimeout(t)
  }, [live])

  const ctx: NbCtx = {
    nb, pid, slug, caps, capsMissing, actions, me, live, runningId, numberOf, ws, openWs, editSpec, rerunFrom, notify, fail,
    collapsed, toggleCollapsed: (id) => setCollapsed((c) => ({ ...c, [id]: !c[id] })),
    selected, setSelected, connections: conns.data ?? [],
    addInside: (parent) => setInside(parent),
  }

  const top = numbered.filter((x) => x.depth === 0)
  const firstStale = top.find((x) => x.cell.stale && x.cell.latest)?.cell.id
  const noAi = nb.cells.filter((c) => !c.needs_ai && c.kind !== 'note').length
  const counted = nb.cells.filter((c) => c.kind !== 'note').length
  const writes = nb.cells.some((c) => c.effect === 'write')
  // the engine writes to the notebook's environment, else the project's default (routes: writes_env)
  const writesEnv = nb.environment || project.default_environment || 'default'
  const defaultKeep: KeepTarget = nb.template_slug === 'stuck-wires' ? 'scheduled_check' : nb.template_slug === 'portal-smoke' ? 'test_suite' : 'workflow'
  const loopFailures = (() => {
    const run = nb.runs[0]
    for (const x of top) {
      const n = x.cell.kind === 'for_each' && x.cell.latest?.run_id === run?.id ? failedRows(x.cell.latest) : 0
      if (n > 0) return { n, cell: x.cell.id }
    }
    return null
  })()
  const statusLine = (() => {
    const r = nb.runs[0]
    if (live) return runningId ? `Running cell ${numberOf(runningId)}` : r?.status === 'waiting' ? 'Waiting' : 'Running'
    if (!r) return 'Not run yet'
    if (r.status === 'needs_you') return `Needs you · cell ${numberOf(r.stop?.cell_id)}`
    if (r.status === 'failed') return `Failed · cell ${numberOf(r.stop?.cell_id)}`
    if (r.status === 'stopped_at_write') return `Stopped before cell ${numberOf(r.stop?.cell_id)}`
    if (r.status === 'done' && loopFailures) return `Needs attention · ${plural(loopFailures.n, 'row')} failed in cell ${numberOf(loopFailures.cell)}`
    // no active run and the latest run is done: say Done even if the notebook row still lags behind
    const word = r.status === 'done' ? 'Done' : r.status === 'cancelled' ? 'Stopped' : STATUS_TEXT[nb.status]
    return `${word} · finished ${fmtTime(r.finished_at ?? r.started_at)}`
  })()
  const dot = live ? 'running' : !nb.runs[0] ? nb.status : nb.runs[0].status === 'done' ? (loopFailures ? 'needs_you' : 'done') : nb.status

  const runBy = last?.triggered_by ?? null
  const usedConns = [...new Set(nb.cells.map((c) => (c.spec as { connection?: string }).connection).filter((x): x is string => !!x))].map(
    (id) => (conns.data ?? []).find((c) => c.id === id || c.key === id)?.name ?? id,
  )
  const checks = useQuery({ queryKey: nbKeys.checks(nb.id), queryFn: () => notebookApi.checks(nb.id), enabled: nb.kept.length > 0, retry: false })

  const wsNode = <Workspace mru={mru} onClose={closeWs} />

  return (
    <NbProvider value={ctx}>
      <div className="nb-scroll nb-fixed glass-page" data-testid="notebook-page">
        <div className="nb-wrap">
          <div className="nb-grid" data-ws={ws.open && !narrow ? 'on' : 'off'}>
            <nav className="nb-outline" data-open={outline ? 'true' : 'false'} aria-label="Outline" data-testid="nb-outline">
              <div className="nb-lp-top">
                {outline && (
                  <ViewSwitch
                    size="sm"
                    aria-label="Left panel"
                    className="nb-lp-seg"
                    value={lp}
                    onChange={setLp}
                    options={[
                      { value: 'steps', label: 'Steps' },
                      { value: 'explorer', label: 'Explorer' },
                    ]}
                  />
                )}
                <button
                  type="button"
                  className="nb-outline-toggle"
                  aria-expanded={outline}
                  aria-label={outline ? 'Hide outline' : 'Show outline'}
                  onClick={() => {
                    setOutline(!outline)
                    writeFlag('outline', !outline)
                  }}
                >
                  {outline ? '‹' : '≡'}
                </button>
              </div>
              {outline && (
                <>
                  <div className="nb-agent">
                    <div className="nb-agent-k">{nb.title}</div>
                    <div className="nb-agent-stt">
                      <span className="nb-dot" data-s={dot} aria-hidden />
                      <b>{statusLine.split(' · ')[0]}</b>
                      {statusLine.includes(' · ') && <span> · {statusLine.split(' · ').slice(1).join(' · ')}</span>}
                    </div>
                    {top.some((x) => x.cell.kind !== 'note') && (
                      <div className="nb-meter" aria-hidden>
                        {top
                          .filter((x) => x.cell.kind !== 'note')
                          .map((x) => {
                            const st = cellStatus(x.cell, nb, runningId)
                            const m = st === 'running' ? 'run' : st === 'fail' ? 'fail' : st === 'need' ? 'need' : x.cell.needs_ai && (st === 'ok' || x.cell.latest) ? 'ai' : st === 'ok' ? 'no' : ''
                            return <i key={x.cell.id} data-m={m} />
                          })}
                      </div>
                    )}
                    <small>
                      {counted > 0 ? `${noAi} of ${counted} cells replay with no AI. ` : 'No cells yet. '}
                      {nb.kept.length > 0 ? `Kept as ${TARGET_LABEL[nb.kept[0].target].toLowerCase()}.` : 'Not saved as a workflow yet.'}
                    </small>
                    <div className="nb-agent-acts">
                      <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" disabled title="Pause · coming">
                        Pause
                      </button>
                      <button
                        type="button"
                        className="gl-btn"
                        data-variant="ghost"
                        data-size="sm"
                        disabled={!live || stopHold || actions.cancel.isPending}
                        onClick={() => nb.active_run_id && actions.cancel.mutate(nb.active_run_id, { onError: fail })}
                      >
                        Stop
                      </button>
                    </div>
                  </div>
                  {lp === 'steps' ? (
                    <div className="nb-ol">
                      <h6>
                        Cells <span>{numbered.length}</span>
                      </h6>
                      <ol>
                        {numbered.map((x) => (
                          <li key={x.cell.id} data-depth={x.depth}>
                            <button
                              type="button"
                              className={selected === x.cell.id ? 'nb-cur' : undefined}
                              onClick={() => {
                                setSelected(x.cell.id)
                                document.getElementById(`cell-${x.cell.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                              }}
                            >
                              <span className="nb-oic" aria-hidden>{KIND_GLYPH[x.cell.kind]}</span>
                              <span className="nb-on">{x.n}</span>
                              <span className="nb-ot">{x.cell.title || KIND_LABEL[x.cell.kind]}</span>
                              <span className="nb-odt" data-s={cellStatus(x.cell, nb, runningId)} data-ai={x.cell.needs_ai ? '' : undefined} aria-hidden />
                            </button>
                          </li>
                        ))}
                      </ol>
                    </div>
                  ) : (
                    <div className="nb-ex">
                      <details className="nb-notes" data-testid="nb-ai-notes">
                        <summary>AI notes</summary>
                        {nb.notes ? (
                          <>
                            <div className="nb-notes-body">{nb.notes}</div>
                            <button
                              type="button"
                              className="gl-btn"
                              data-variant="ghost"
                              data-size="sm"
                              disabled={actions.patch.isPending}
                              onClick={() => actions.patch.mutate({ notes: '' }, { onError: fail })}
                              data-testid="nb-ai-notes-clear"
                            >
                              Clear
                            </button>
                          </>
                        ) : (
                          <p className="nb-faint">No notes yet</p>
                        )}
                      </details>
                      <details open>
                        <summary>Connections used</summary>
                        {usedConns.length === 0 ? <p className="nb-faint">None</p> : usedConns.map((c) => <p key={c}>⛁ {c}</p>)}
                      </details>
                      <details open>
                        <summary>Runs</summary>
                        {nb.runs.length === 0 ? (
                          <p className="nb-faint">Not run yet</p>
                        ) : (
                          nb.runs.slice(0, 8).map((r) => (
                            <p key={r.id}>
                              run {r.n} · {r.status.replace(/_/g, ' ')} <span className="nb-faint">{fmtTime(r.started_at)}</span>
                            </p>
                          ))
                        )}
                      </details>
                      <details open>
                        <summary>Kept</summary>
                        {nb.kept.length === 0 ? <p className="nb-faint">Nothing kept yet</p> : nb.kept.map((k) => <p key={k.workflow_id + k.at}>↳ {TARGET_LABEL[k.target]}</p>)}
                      </details>
                    </div>
                  )}
                </>
              )}
            </nav>

            <main className="nb-main min-w-0">
              <header className="nb-head">
                <div className="nb-crumb">
                  <span>{project.name}</span>
                  <span className="nb-sl" aria-hidden>›</span>
                  <Link to="/p/$project/notebooks" params={{ project: slug }}>Notebooks</Link>
                  <span className="nb-sl" aria-hidden>›</span>
                  <b className="truncate">{nb.title}</b>
                </div>
                <div className="nb-eyebrow">
                  <span className="nb-chip nb-taskc">
                    ▤ Notebook <i>· {nb.kept.length > 0 ? 'kept as a workflow' : 'not a workflow'}</i>
                  </span>
                  <span className="nb-chip">
                    {nb.runs.length === 0 ? 'not run yet' : nb.runs.length === 1 ? 'ran once' : `ran ${nb.runs.length} times`}
                    {last && ` · ${dayOf(last.started_at)} ${hm(last.started_at)}${last.finished_at ? `–${hm(last.finished_at)}` : ''}`}
                  </span>
                  {writes && (
                    <span className="nb-chip" data-tone={isProdEnv(writesEnv) ? 'prod' : 'warn'} title={`write cells change data in ${writesEnv}`}>
                      writes · {isProdEnv(writesEnv) ? writesEnv.toUpperCase() : writesEnv}
                    </span>
                  )}
                </div>
                <h1 className="nb-title">
                  <input
                    value={title}
                    aria-label="Notebook title"
                    onChange={(e) => setTitle(e.target.value)}
                    onBlur={() => title.trim() && title !== nb.title && actions.patch.mutate({ title: title.trim() }, { onError: fail })}
                    onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                  />
                </h1>
                <div className="nb-status" data-testid="nb-status">
                  <span className="nb-by">
                    <Avatar name={nb.created_by ?? '?'} />
                    {runBy && runBy !== nb.created_by && <span className="nb-av-2"><Avatar name={runBy} /></span>}
                    <span>
                      <b>{nb.created_by ?? 'unknown'}</b> started it
                    </span>
                  </span>
                  <span>· {fmtTime(nb.updated_at)}</span>
                  <span>
                    · <span className="nb-dot" data-s={dot} aria-hidden />
                    {statusLine}
                  </span>
                  {nb.kept.map((k) => (
                    <a key={k.workflow_id + k.at} className="nb-pill" href={`/p/${encodeURIComponent(slug)}/workflows/${encodeURIComponent(k.workflow_id)}?view=page`}>
                      kept as {TARGET_LABEL[k.target].toLowerCase()}
                    </a>
                  ))}
                  {checks.data && checks.data.runs.length > 0 && (
                    <CheckStrip
                      summary
                      data-testid="nb-checks"
                      results={[...checks.data.runs].reverse().map((r) => ({ result: r.status === 'completed' || r.status === 'success' ? 'pass' : r.status === 'running' ? 'none' : 'fail', label: `${r.status} · ${fmtTime(r.start_time)}` }))}
                    />
                  )}
                </div>
                {counted > 0 && (
                  <p className="nb-gen" title="cells that run without AI">
                    <span className="nb-g0" aria-hidden>⌁</span> <b>{noAi} of {counted} cells</b> replay with no AI
                    {counted - noAi > 0 ? ` · ${counted - noAi} need${counted - noAi === 1 ? 's' : ''} AI` : ' · deterministic, re-runnable'}
                  </p>
                )}
                <div className="nb-acts">
                  <SplitButton
                    label={`Keep as ${TARGET_LABEL[defaultKeep].toLowerCase()}`}
                    ariaLabel={`Keep as… ${TARGET_LABEL[defaultKeep].toLowerCase()}`}
                    variant="primary"
                    onClick={() => setKeep({ open: true, target: defaultKeep })}
                    items={(['workflow', 'test_suite', 'scheduled_check', 'dataset', 'report'] as KeepTarget[]).map((t) => ({ id: t, label: TARGET_LABEL[t], onSelect: () => setKeep({ open: true, target: t }) }))}
                    menuLabel="Keep as a…"
                    data-testid="nb-keep-btn"
                  />
                  {live ? (
                    <button type="button" className="gl-btn" data-variant="ghost" disabled={stopHold || actions.cancel.isPending} onClick={() => nb.active_run_id && actions.cancel.mutate(nb.active_run_id, { onError: fail })} data-testid="nb-stop">
                      ■ Stop
                    </button>
                  ) : (
                    <button type="button" className="gl-btn" data-variant="ghost" onClick={runAll} disabled={actions.run.isPending || nb.cells.length === 0} data-testid="nb-run">
                      ▶ Run all
                    </button>
                  )}
                  <button type="button" className="gl-btn" data-variant="ghost" onClick={() => openWs('report')}>
                    Report
                  </button>
                  <button type="button" className="gl-btn" data-variant="ghost" onClick={() => openWs('trace')}>
                    History
                  </button>
                  <Menu.Root>
                    <Menu.Trigger className="gl-btn" data-variant="ghost" aria-label="More" data-testid="nb-more">⋯</Menu.Trigger>
                    <Menu.Portal>
                      <Menu.Positioner sideOffset={6} align="end" className="z-[70]">
                        <Menu.Popup className="gl-overlay gl-menu">
                          <Menu.Item className="gl-menu-item" onClick={() => openWs('report')}>{TAB_LABEL.report}</Menu.Item>
                          <Menu.Item className="gl-menu-item" onClick={() => openWs('trace')}>{TAB_LABEL.trace}</Menu.Item>
                          <Menu.Item
                            className="gl-menu-item"
                            onClick={() => {
                              const n = window.prompt?.('Your name in attribution lines', me)
                              if (n !== null && n !== undefined) {
                                setMyName(n)
                                setMe(myName())
                              }
                            }}
                          >
                            Signed as {me}…
                          </Menu.Item>
                          <Menu.Item className="gl-menu-item" disabled>
                            Share <span className="gl-hint">coming</span>
                          </Menu.Item>
                          <Menu.Item className="gl-menu-item" disabled={live} onClick={() => setConfirmDelete(true)}>
                            Delete notebook
                          </Menu.Item>
                        </Menu.Popup>
                      </Menu.Positioner>
                    </Menu.Portal>
                  </Menu.Root>
                </div>
                {confirmDelete && (
                  <div className="nb-rerun" data-testid="nb-confirm-delete">
                    Delete “{nb.title}” and its runs? Kept workflows stay.
                    <span className="nb-sp" />
                    <button type="button" className="gl-btn" data-size="sm" onClick={() => setConfirmDelete(false)}>Cancel</button>
                    <button
                      type="button"
                      className="gl-btn"
                      data-variant="primary"
                      data-size="sm"
                      onClick={() => del.mutate(nb.id, { onSuccess: () => void navigate({ to: '/p/$project/notebooks', params: { project: slug } }), onError: fail })}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </header>

              <RerunBar />

              <ApiSuiteBar importOpen={importOpen} setImportOpen={setImportOpen} onKeep={() => setKeep({ open: true, target: 'test_suite' })} />

              <div className="nb-cells" data-testid="nb-cells">
                {top.length === 0 && !draft && (
                  <div className="nb-card nb-faint" data-testid="nb-empty">An empty notebook. Add a block below: a query, an HTTP call, a rule…</div>
                )}
                {top.map((x) => (
                  <CellView
                    key={x.cell.id}
                    cell={x.cell}
                    n={x.n}
                    firstStale={firstStale === x.cell.id}
                    nested={
                      x.cell.kind === 'for_each'
                        ? numbered.filter((y) => y.parent?.id === x.cell.id).map((y) => <CellView key={y.cell.id} cell={y.cell} n={y.n} firstStale={false} />)
                        : undefined
                    }
                  />
                ))}
              </div>

              <Composer draft={draft} setDraft={setDraft} inside={inside} setInside={setInside} onImport={() => setImportOpen(true)} />
            </main>

            {ws.open && !narrow ? (
              wsNode
            ) : !narrow ? (
              <button type="button" className="nb-wsrail" onClick={() => openWs(ws.tab, ws.cellId)} data-testid="nb-wsrail" aria-label="Open workspace">
                <span className="nb-wsrail-dot" data-live={live ? '' : undefined} aria-hidden />
                Workspace · {TAB_LABEL[ws.tab].replace(/^\S+\s/, '')}
              </button>
            ) : null}
          </div>
        </div>
      </div>
      {narrow && (
        <GlassSheet open={ws.open} onOpenChange={(o) => (o ? openWs(ws.tab, ws.cellId) : closeWs())} side="right" title="Workspace">
          {wsNode}
        </GlassSheet>
      )}
      <KeepDrawer open={keep.open} onOpenChange={(o) => setKeep((k) => ({ ...k, open: o }))} initialTarget={keep.target} />
    </NbProvider>
  )
}
