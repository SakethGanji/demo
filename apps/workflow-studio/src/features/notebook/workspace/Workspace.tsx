/**
 * The contextual workspace: closed until needed, at most 4 tabs (current, most recently used, then the rest) + "+N", a "from cell N" pill
 * linking back to the cell. Every tab reads a real route; routes not built yet say so plainly.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Menu } from '@base-ui/react/menu'
import { useQuery } from '@tanstack/react-query'
import { Chip, formatCell } from '@/shared/glass'
import { errorMessage } from '@/shared/api/http'
import { isNotBuilt, notebookApi } from '../api'
import { useNb } from '../context'
import { nbKeys } from '../hooks'
import { asRows, fmtMs, fmtTime, numberCells, plural, tabFor, type WsTab } from '../lib/cells'
import type { Cell, WorkflowSpec } from '../types'
import { BrowserBody, ChangeBody } from '../cells/Bodies'
import { StatusPill } from '../cells/Look'
import { RowsTable } from '../cells/RowsTable'
import { ApiTab } from './ApiResponse'
import { isProdEnv, isStatusColumn } from '../lib/look'
import { useProjectCtx } from '@/features/project/ProjectContext'

export const TAB_LABEL: Record<WsTab, string> = {
  browser: '◱ Browser', results: '▦ Results', data: '▤ Data', change: '± Change review', api: '⇄ API response',
  workflow: '↳ Workflow run', trace: '⋮ Trace', report: '¶ Report',
}

/** "◱ Browser" -> glyph + name, for the icon tab. */
export function tabParts(t: WsTab): { ic: string; name: string } {
  const [ic, ...rest] = TAB_LABEL[t].split(' ')
  return { ic, name: rest.join(' ') }
}

/** `env`: the environment write cells change, shown on the Change review tab (red for production, amber otherwise). */
function TabFace({ t, env }: { t: WsTab; env?: string | null }) {
  const { ic, name } = tabParts(t)
  const prod = isProdEnv(env)
  return (
    <>
      <span className="nb-ic">{ic}</span> {name}
      {env && t === 'change' && (
        <span className="nb-bd" data-tone={prod ? undefined : 'warn'} title={`writes to ${env}`}>
          {prod ? 'PROD' : env}
        </span>
      )}
    </>
  )
}

/** Tabs this notebook can show: one per kind of cell it has, plus trace and report. */
export function availableTabs(cells: Cell[]): WsTab[] {
  const set = new Set<WsTab>()
  numberCells(cells).forEach((x) => {
    const t = tabFor(x.cell.kind)
    if (t) set.add(t)
  })
  set.add('trace')
  set.add('report')
  const order: WsTab[] = ['browser', 'results', 'data', 'change', 'api', 'workflow', 'trace', 'report']
  return order.filter((t) => set.has(t))
}

export function Workspace({ mru, onClose }: { mru: WsTab[]; onClose: () => void }) {
  const { nb, ws, openWs, numberOf, live, runningId } = useNb()
  const { project } = useProjectCtx()
  const writesEnv = nb.cells.some((c) => c.effect === 'write') ? nb.environment || project.default_environment || null : null
  const all = availableTabs(nb.cells)
  // the current tab, then the most recently used, then the rest in order: at most 4 show as tabs
  const rec = [...new Set([ws.tab, ...mru, ...all])].filter((t) => all.includes(t))
  const vis = rec.slice(0, 4)
  const rest = all.filter((t) => !vis.includes(t))
  // the cell this tab shows: the linked one, else the last cell with a result of that kind
  const cell = useMemo(() => {
    const byId = ws.cellId ? numberCells(nb.cells).find((x) => x.cell.id === ws.cellId)?.cell : undefined
    if (byId && tabFor(byId.kind) === ws.tab) return byId
    const list = numberCells(nb.cells).map((x) => x.cell).filter((c) => tabFor(c.kind) === ws.tab)
    return [...list].reverse().find((c) => c.latest) ?? list[list.length - 1] ?? null
  }, [nb.cells, ws.cellId, ws.tab])

  return (
    <section className="nb-ws" aria-label="Workspace" data-testid="nb-workspace" data-tab={ws.tab}>
      <div className="nb-ws-head" role="tablist" aria-label="Workspace tabs">
        {vis.map((t) => (
          <button key={t} type="button" role="tab" className="nb-ws-tab" aria-selected={t === ws.tab} onClick={() => openWs(t, t === ws.tab ? ws.cellId : null)}>
            <TabFace t={t} env={writesEnv} />
          </button>
        ))}
        {rest.length > 0 && (
          <Menu.Root>
            <Menu.Trigger className="nb-ws-tab" aria-label={`${rest.length} more tabs`}>+{rest.length}</Menu.Trigger>
            <Menu.Portal>
              <Menu.Positioner sideOffset={4} align="start" className="z-[70]">
                <Menu.Popup className="gl-overlay gl-menu">
                  {rest.map((t) => (
                    <Menu.Item key={t} className="gl-menu-item" onClick={() => openWs(t, null)}>
                      <TabFace t={t} env={writesEnv} />
                    </Menu.Item>
                  ))}
                </Menu.Popup>
              </Menu.Positioner>
            </Menu.Portal>
          </Menu.Root>
        )}
        <span style={{ flex: 1 }} />
        <button type="button" className="gl-icon-btn" aria-label="Close workspace" title="Close workspace" onClick={onClose} data-testid="nb-ws-close">
          ⇥
        </button>
      </div>
      <div className="nb-ws-body" role="tabpanel">
        <div className="nb-ws-sub">
          <span className="nb-ws-k">
            {tabParts(ws.tab).name}
            {cell && ws.tab !== 'trace' && ws.tab !== 'report' ? ` · ${cell.title || cell.name}` : ` · ${nb.title}`}
          </span>
          {live && (!cell || runningId === cell.id || ws.tab === 'trace') ? (
            <span className="nb-live">Live</span>
          ) : (
            cell?.latest && ws.tab !== 'trace' && ws.tab !== 'report' && <span className="nb-faint">{fmtTime(cell.latest.created_at)}</span>
          )}
          <span style={{ flex: 1 }} />
          {cell && ws.tab !== 'trace' && ws.tab !== 'report' && (
            <button
              type="button"
              className="nb-from"
              data-testid="nb-ws-from"
              onClick={() => document.getElementById(`cell-${cell.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
            >
              from cell {numberOf(cell.id)}
            </button>
          )}
        </div>
        {ws.tab === 'results' || ws.tab === 'data' ? <RowsTab cell={cell} /> : null}
        {ws.tab === 'change' && (cell ? <ChangeTab cell={cell} /> : <Empty>No change cell yet.</Empty>)}
        {ws.tab === 'browser' && (cell ? <BrowserTab cell={cell} /> : <Empty>No browser cell yet.</Empty>)}
        {ws.tab === 'api' && (cell ? <ApiTab cell={cell} Empty={Empty} /> : <Empty>No HTTP cell yet.</Empty>)}
        {ws.tab === 'workflow' && (cell ? <WorkflowTab cell={cell} /> : <Empty>No workflow cell yet.</Empty>)}
        {ws.tab === 'trace' && <TraceTab />}
        {ws.tab === 'report' && <ReportTab />}
      </div>
    </section>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="nb-faint p-2">{children}</div>
}

function NotYet({ what, e }: { what: string; e: unknown }) {
  return <Empty>{isNotBuilt(e) ? `${what} is not available on this engine yet.` : errorMessage(e)}</Empty>
}

const PAGE = 100

function RowsTab({ cell }: { cell: Cell | null }) {
  const [offset, setOffset] = useState(0)
  const [acting, setActing] = useState(false)
  useEffect(() => setActing(false), [cell?.id])
  useEffect(() => setOffset(0), [cell?.id])
  const runId = cell?.latest?.run_id
  const q = useQuery({
    queryKey: [...nbKeys.rows(cell?.id ?? '', runId), offset],
    queryFn: () => notebookApi.rows(cell!.id, { run: runId, offset, limit: PAGE }),
    enabled: !!cell?.latest && !!asRows(cell.latest.output),
    retry: false,
  })
  if (!cell) return <Empty>No table yet.</Empty>
  if (!cell.latest) return <Empty>Run the notebook to see rows here.</Empty>
  if (!asRows(cell.latest.output)) return <Empty>This cell has no rows.</Empty>
  if (q.isError) return <NotYet what="Rows" e={q.error} />
  if (!q.data) return <Empty>Loading rows…</Empty>
  const { columns, rows, total } = q.data
  const selectable = cell.kind === 'query' || cell.kind === 'data' || cell.kind === 'rule'
  const statusCols = new Set(columns.filter((c) => isStatusColumn(c, rows)))
  return (
    <div data-testid="nb-ws-rows">
      <div className="nb-row mb-2">
        <b className="text-[13px]">{plural(total, 'row')}</b>
        {!acting && <span className="nb-faint">showing {total ? offset + 1 : 0}–{offset + rows.length}</span>}
        <span className="nb-sp" />
        {selectable && total > 0 && (
          <button type="button" className="gl-btn" data-size="sm" aria-pressed={acting} onClick={() => setActing(!acting)} data-testid="nb-ws-select">
            {acting ? 'Back to pages' : 'Select rows'}
          </button>
        )}
      </div>
      {acting ? <RowsTable cell={cell} /> : <>
      <div className="nb-row mb-2">
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
        <button type="button" className="gl-btn" data-size="sm" disabled={offset + rows.length >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
      </div>
      <div className="gl-table">
        <div className="gl-table-scroll">
          <table>
            <thead>
              <tr>{columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {columns.map((c) => {
                    const f = formatCell(r[c])
                    return <td key={c} data-num={f.num ? '' : undefined} data-null={f.nil ? '' : undefined} title={f.text}>{statusCols.has(c) && typeof r[c] === 'string' ? <StatusPill value={String(r[c])} /> : f.text}</td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      </>}
    </div>
  )
}

function ChangeTab({ cell }: { cell: Cell }) {
  const o = (cell.latest?.output ?? {}) as Record<string, unknown>
  if (!cell.latest) return <Empty>Run the notebook: the change is proposed here before anything is written.</Empty>
  return (
    <div data-testid="nb-ws-change">
      <div className="nb-row mb-2">
        <Chip mono size="sm">{String(o.table ?? '')}</Chip>
        <span className="nb-faint">{String(o.summary ?? '')}</span>
        {o.idempotency_key ? <span className="nb-faint nb-mono">key {String(o.idempotency_key).slice(0, 12)}</span> : null}
      </div>
      <ChangeBody cell={cell} />
    </div>
  )
}

const SPECIAL_KEYS = new Set(['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'])

function BrowserTab({ cell }: { cell: Cell }) {
  const { runningId, live } = useNb()
  const running = runningId === cell.id
  const [frame, setFrame] = useState<string | null>(null)
  const [frameNote, setFrameNote] = useState<string | null>(null)
  const urlRef = useRef<string | null>(null)
  const [ctrlNote, setCtrlNote] = useState<string | null>(null)
  // take over: the scripted steps pause and this frame relays clicks, typing, keys and scrolling to the live page
  const [driving, setDriving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(0)
  const typed = useRef('')
  const typeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const wheel = useRef({ dx: 0, dy: 0, t: undefined as ReturnType<typeof setTimeout> | undefined })
  const padRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!running) setDriving(false)
  }, [running])
  useEffect(() => {
    let stop = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      try {
        const b = await notebookApi.frame(cell.id)
        if (stop) return
        if (b.size > 0) {
          const u = URL.createObjectURL(b)
          if (urlRef.current) URL.revokeObjectURL(urlRef.current)
          urlRef.current = u
          setFrame(u)
          setFrameNote(null)
        } else if (!urlRef.current) setFrameNote('No frame yet · the browser shows here while the cell runs.')
      } catch (e) {
        if (!stop) setFrameNote(isNotBuilt(e) ? 'Live frame is not available on this engine yet.' : errorMessage(e))
        return
      }
      if (!stop && running) timer = setTimeout(tick, driving ? 350 : 1000)
    }
    void tick()
    return () => {
      stop = true
      if (timer) clearTimeout(timer)
    }
  }, [cell.id, running, driving, cell.latest?.id])
  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
  }, [])
  // the run ended (or was stopped elsewhere) while you were driving: there is no live page left to hand back
  useEffect(() => {
    if (!running) setDriving(false)
  }, [running])
  // what the runner did not keep (e.g. "video not kept: take over used")
  const rawNotes = ((cell.latest?.output ?? {}) as Record<string, unknown>).notes
  const artNotes = Array.isArray(rawNotes) ? rawNotes.filter((n): n is string => typeof n === 'string') : []
  const arts = useQuery({ queryKey: ['notebook-artifacts', cell.id, cell.latest?.id ?? ''], queryFn: () => notebookApi.artifacts(cell.id), enabled: !!cell.latest && !running, retry: false })
  const control = async (a: 'pause' | 'resume' | 'step' | 'stop' | 'takeover' | 'handback') => {
    setCtrlNote(null)
    setBusy(true)
    try {
      if (a === 'handback') await flushTyped()
      await notebookApi.control(cell.id, a)
      if (a === 'takeover') {
        setDriving(true)
        setTimeout(() => padRef.current?.focus(), 50)
      }
      if (a === 'handback' || a === 'stop') setDriving(false)
    } catch (e) {
      setCtrlNote(isNotBuilt(e) ? 'Controls are not available on this engine yet.' : errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  const send = async (body: Parameters<typeof notebookApi.input>[1]) => {
    try {
      await notebookApi.input(cell.id, body)
      setSent((n) => n + 1)
    } catch (e) {
      setCtrlNote(errorMessage(e))
    }
  }
  const flushTyped = async () => {
    if (typeTimer.current) clearTimeout(typeTimer.current)
    const t = typed.current
    typed.current = ''
    if (t) await send({ type: 'type', text: t })
  }
  const onClick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!driving) return
    const b = e.currentTarget.getBoundingClientRect()
    const clamp = (v: number) => Math.min(1, Math.max(0, v))
    void flushTyped().then(() => send({ type: 'click', x: clamp((e.clientX - b.left) / b.width), y: clamp((e.clientY - b.top) / b.height) }))
    padRef.current?.focus()
  }
  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!driving || e.metaKey || e.ctrlKey || e.altKey) return
    if (e.key.length === 1) {
      e.preventDefault()
      typed.current += e.key
      if (typeTimer.current) clearTimeout(typeTimer.current)
      typeTimer.current = setTimeout(() => void flushTyped(), 250)
    } else if (SPECIAL_KEYS.has(e.key)) {
      e.preventDefault()
      void flushTyped().then(() => send({ type: 'key', key: e.key }))
    }
  }
  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (!driving) return
    const w = wheel.current
    w.dx += e.deltaX
    w.dy += e.deltaY
    if (w.t) return
    w.t = setTimeout(() => {
      const dx = Math.max(-5000, Math.min(5000, Math.round(w.dx)))
      const dy = Math.max(-5000, Math.min(5000, Math.round(w.dy)))
      w.dx = 0
      w.dy = 0
      w.t = undefined
      if (dx || dy) void send({ type: 'scroll', dx, dy })
    }, 150)
  }
  return (
    <div className="flex flex-col gap-3" data-testid="nb-ws-browser">
      <div
        ref={padRef}
        className="nb-takeover"
        data-driving={driving ? '' : undefined}
        tabIndex={driving ? 0 : -1}
        onKeyDown={onKey}
        onWheel={onWheel}
        aria-label={driving ? 'Live page: you are driving. Click, type, press keys and scroll here.' : undefined}
        data-testid="nb-takeover-pad"
      >
        {frame ? (
          <img className="nb-frame" src={frame} alt="Live browser frame" data-testid="nb-frame" onClick={onClick} draggable={false} />
        ) : (
          <div className="nb-card nb-faint">{frameNote ?? 'Loading frame…'}</div>
        )}
        {driving && <span className="nb-takeover-tag">You are driving · {plural(sent, 'input')} sent</span>}
      </div>
      <div className="nb-row">
        {(['pause', 'resume', 'step', 'stop'] as const).map((a) => (
          <button key={a} type="button" className="gl-btn" data-size="sm" disabled={!running || !live || driving || busy} onClick={() => control(a)}>
            {a[0].toUpperCase() + a.slice(1)}
          </button>
        ))}
        <span className="nb-sp" />
        {driving ? (
          <button type="button" className="gl-btn" data-size="sm" data-variant="primary" disabled={busy} onClick={() => control('handback')} data-testid="nb-handback">
            Hand back
          </button>
        ) : (
          <button type="button" className="gl-btn" data-size="sm" disabled={!running || !live || busy} title={running ? 'Pause the steps and drive the live page yourself' : 'Take over works while this browser cell runs'} onClick={() => control('takeover')} data-testid="nb-takeover">
            Take over
          </button>
        )}
      </div>
      {driving && <div className="nb-faint">The recorded steps are paused. Click the page, type, press Enter or Tab, scroll. Hand back resumes the steps from where they paused; typed text is not logged, and the trace, video and screenshots are not kept from here on.</div>}
      {ctrlNote && <div className="nb-faint" data-testid="nb-ctrl-note">{ctrlNote}</div>}
      <BrowserBody cell={cell} />
      {arts.data && arts.data.length > 0 && (
        <div>
          <div className="kind-label mb-1">artifacts</div>
          {arts.data.map((a) => (
            <div key={a.name} className="nb-row">
              <a className="nb-linkbtn text-[12.5px]" href={notebookApi.artifactUrl(cell.id, a.name)} download>{a.name}</a>
              <span className="nb-faint">{a.content_type} · {(a.size / 1024).toFixed(0)} KB</span>
            </div>
          ))}
        </div>
      )}
      {artNotes.length > 0 && (
        <div data-testid="nb-artifact-notes">
          {artNotes.map((n) => <div key={n} className="nb-faint">{n}</div>)}
        </div>
      )}
      {arts.isError && <NotYet what="Artifacts" e={arts.error} />}
    </div>
  )
}

function WorkflowTab({ cell }: { cell: Cell }) {
  const { slug } = useNb()
  const s = cell.spec as WorkflowSpec
  const r = cell.latest
  const o = (r?.output ?? {}) as Record<string, unknown>
  const exec = (o.execution_id as string | undefined) ?? r?.execution_id ?? undefined
  const href = `/p/${encodeURIComponent(slug)}/workflows/${encodeURIComponent(s.workflow_id)}?view=page${exec ? `&execution=${encodeURIComponent(exec)}` : ''}`
  return (
    <div className="flex flex-col gap-2" data-testid="nb-ws-workflow">
      <div className="nb-row">
        <Chip mono size="sm">{String(o.status ?? r?.status ?? 'not run')}</Chip>
        {o.last_node ? <span className="nb-faint">last step {String(o.last_node)}</span> : null}
        {r && <span className="nb-faint">{fmtMs(r.ms)}</span>}
      </div>
      <a className="nb-linkbtn text-[13px]" href={href} data-testid="nb-ws-workflow-link">
        Open this workflow's Page view{exec ? ' at this run' : ''} ▸
      </a>
      {asRows(r?.output ?? null) && <RowsTab cell={cell} />}
    </div>
  )
}

function TraceTab() {
  const { nb, numberOf } = useNb()
  const q = useQuery({ queryKey: [...nbKeys.trace(nb.id), nb.runs[0]?.id ?? '', nb.runs[0]?.status ?? ''], queryFn: () => notebookApi.trace(nb.id), retry: false })
  if (q.isError) return <NotYet what="Trace" e={q.error} />
  if (!q.data) return <Empty>Loading trace…</Empty>
  if (q.data.length === 0) return <Empty>Nothing has run yet.</Empty>
  return (
    <div className="nb-trace" data-testid="nb-ws-trace">
      {q.data.map((t, i) => (
        <div key={i}>
          <span className="nb-mono nb-faint">{fmtTime(t.ts)}</span>
          <span className="nb-mono nb-faint">{t.cell_id ? numberOf(t.cell_id) : t.run_n ? `r${t.run_n}` : ''}</span>
          <span className="min-w-0">{t.text}</span>
          <span className="nb-mono nb-faint">{fmtMs(t.ms)}</span>
        </div>
      ))}
    </div>
  )
}

function ReportTab() {
  const { nb, notify } = useNb()
  const q = useQuery({ queryKey: [...nbKeys.report(nb.id), nb.runs[0]?.id ?? '', nb.runs[0]?.status ?? ''], queryFn: () => notebookApi.report(nb.id), retry: false })
  if (q.isError) return <NotYet what="The report" e={q.error} />
  if (!q.data) return <Empty>Writing the report…</Empty>
  return (
    <div data-testid="nb-ws-report">
      <div className="nb-row mb-2">
        <span className="kind-label">report.md · written from the run, no AI</span>
        <span className="nb-sp" />
        <button
          type="button"
          className="gl-btn"
          data-size="sm"
          onClick={() => {
            void navigator.clipboard?.writeText(q.data.markdown).then(() => notify('Report copied'), () => notify('Copy failed', 'fail'))
          }}
        >
          Copy
        </button>
      </div>
      <div className="nb-report">{q.data.markdown}</div>
    </div>
  )
}
