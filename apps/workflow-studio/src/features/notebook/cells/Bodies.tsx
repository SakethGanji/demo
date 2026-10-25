/** Per-kind result bodies under a cell's sentence: tables, diffs, human-step cards, HTTP response, browser recipe. */
import { useState } from 'react'
import { Chip, DataTablePreview, DiffBlock, StatusMark, ViewSwitch, type DiffLine } from '@/shared/glass'
import { errorMessage, isApiError } from '@/shared/api/http'
import { notebookApi, isNotBuilt } from '../api'
import { useNb } from '../context'
import { asRows, countOnly, fmtMs, fmtTime, KIND_LABEL, plural } from '../lib/cells'
import { isStatusColumn, moneyTotal, rowNoun } from '../lib/look'
import { Avatar, ByName, StatusPill, useExecution, useWorkflowName } from './Look'
import { RowsTable } from './RowsTable'
import { HttpResultBody } from './Http'
import type { Cell, CellResult, ForEachMode, ForEachPreview, ForEachSpec, WaitSpec, WorkflowSpec } from '../types'

type Out = Record<string, unknown>
const out = (r: CellResult | null): Out => (r?.output ?? {}) as Out

/** Cell error card; browser_unavailable and friends are named plainly. */
export function ErrorCard({ r }: { r: CellResult }) {
  if (!r.error) return null
  const code = String(r.error.code)
  const plain: Record<string, string> = {
    browser_unavailable: 'The browser runner (jobs-service) is not connected to this engine.',
    llm_unavailable: 'AI is not available on this engine; Ask needs it.',
    unbound_credential: 'This step needs a credential that is not bound yet.',
    bad_ref: 'A reference to another cell could not be resolved.',
  }
  return (
    <div className="nb-card" data-tone="fail" data-testid="nb-cell-error">
      <div className="nb-row">
        <span className="nb-mono" style={{ color: 'var(--destructive)' }}>{code}</span>
        <span className="nb-sp" />
      </div>
      <div className="mt-1">{plain[code] ? `${plain[code]} ` : ''}{r.error.message}</div>
    </div>
  )
}

export function RowsBody({ cell }: { cell: Cell }) {
  const { openWs, connections } = useNb()
  const [acting, setActing] = useState(false)
  const r = cell.latest
  const rows = asRows(r?.output ?? null)
  const count = countOnly(cell)
  if (count !== null)
    return (
      <div className="nb-well" data-testid="nb-count">
        <div className="nb-wh">
          <span className="nb-count">
            <b>{count.toLocaleString('en-US')}</b> {count === 1 ? 'row' : 'rows'}
          </span>
          <span className="nb-wh-s">{`rule · ${cell.name}`}</span>
        </div>
      </div>
    )
  if (!rows) return null
  const tab = cell.kind === 'data' ? 'data' : 'results'
  const spec = cell.spec as { connection?: string; sql?: string }
  const connId = spec.connection
  const source =
    cell.kind === 'query'
      ? (connections.find((c) => c.id === connId || c.key === connId)?.name ?? connId ?? 'query')
      : cell.kind === 'data'
        ? 'inline data'
        : `rule · ${cell.name}`
  const o = out(r)
  const mismatches = cell.kind === 'rule' && typeof o.mismatches === 'number' ? o.mismatches : null
  const compared = mismatches !== null ? mismatches + Number(o.matched ?? 0) : null
  const noun = cell.kind === 'query' ? rowNoun(spec.sql) : { one: 'row', many: 'rows' }
  const total = moneyTotal(rows.columns, rows.rows, rows.row_count)
  const statusCols = new Set(rows.columns.filter((c) => isStatusColumn(c, rows.rows)))
  return (
    <div className="nb-well" data-acting={acting ? '' : undefined}>
      <div className="nb-wh">
        <span className="nb-wh-t">
          {compared !== null ? (
            <>
              {compared.toLocaleString('en-US')} compared
              <span className="nb-wh-chip" data-tone={mismatches ? 'warn' : 'ok'}>
                {mismatches ? plural(mismatches, 'mismatch', 'mismatches') : 'all match'}
              </span>
            </>
          ) : (
            plural(rows.row_count, noun.one, noun.many)
          )}
          {total && compared === null && <span className="nb-wh-chip" title="sum of every row">{total}</span>}
          {rows.truncated && <span className="nb-wh-cap">capped at {rows.cap ?? ''}</span>}
        </span>
        <span className="nb-wh-s">{source}</span>
        {rows.row_count > 0 && (
          <button type="button" className="nb-linkbtn text-[12px]" aria-pressed={acting} onClick={() => setActing(!acting)} data-testid="nb-select-rows">
            {acting ? 'Done' : 'Select rows'}
          </button>
        )}
      </div>
      {acting ? (
        <RowsTable cell={cell} compact />
      ) : (
        <DataTablePreview
          columns={rows.columns}
          rows={rows.rows}
          total={rows.row_count}
          onMore={() => openWs(tab, cell.id)}
          renderCell={(c, v) => (statusCols.has(c) && typeof v === 'string' ? <StatusPill value={v} /> : undefined)}
          rowTone={mismatches ? () => 'warn' : undefined}
          data-testid="nb-table"
        />
      )}
    </div>
  )
}

export function ChangeBody({ cell }: { cell: Cell }) {
  const { actions, me, fail, live, rerunFrom } = useNb()
  // a 409 proposal_stale marks *this* proposal out of date; any new proposal or result (new id / run) clears it
  const [staleKey, setStaleKey] = useState<string | null>(null)
  const r = cell.latest
  if (!r) return null
  const resultKey = `${r.id}:${r.run_id}:${r.status}`
  const outOfDate = staleKey === resultKey
  const o = out(r)
  const diff = (o.diff as { key: unknown; changes: { column: string; before: unknown; after: unknown }[] }[] | undefined) ?? []
  const lines: DiffLine[] = []
  diff.slice(0, 6).forEach((d) => {
    lines.push({ kind: 'ctx', k: '', text: `row ${typeof d.key === 'object' ? JSON.stringify(d.key) : String(d.key)}` })
    d.changes.forEach((c) => {
      lines.push({ kind: 'del', k: c.column, text: JSON.stringify(c.before) })
      lines.push({ kind: 'add', k: c.column, text: JSON.stringify(c.after) })
    })
  })
  const pending = r.status === 'needs_approval'
  const applied = r.status === 'applied'
  return (
    <>
      {diff.length > 0 && <DiffBlock lines={lines} title={`${plural(diff.length, 'row')} would change`} meta={String(o.table ?? '')} />}
      {diff.length > 6 && <div className="nb-faint mt-1">+ {diff.length - 6} more rows in Change review</div>}
      {pending && (
        <div className="nb-card" data-tone="need" data-testid="nb-change-pending">
          <div className="nb-row">
            <span>Nothing is written until you approve. {String(o.summary ?? '')}</span>
            <span className="nb-sp" />
            <button type="button" className="gl-btn" data-size="sm" disabled={actions.discard.isPending} onClick={() => actions.discard.mutate({ cellId: cell.id, by: me }, { onError: fail })}>
              Discard
            </button>
            <button type="button" className="gl-btn" data-variant="primary" data-size="sm" disabled={actions.apply.isPending} onClick={() =>
                actions.apply.mutate({ cellId: cell.id, by: me }, { onError: (e) => (isApiError(e, 'proposal_stale') ? setStaleKey(resultKey) : fail(e)) })
              }
              data-testid="nb-change-apply"
            >
              Approve &amp; apply
            </button>
          </div>
        </div>
      )}
      {pending && (outOfDate || cell.stale) && (
        <div className="nb-card" data-tone="fail" data-testid="nb-change-stale">
          <div className="nb-row">
            <span>This change is out of date: this cell or one above it was edited after the proposal. Nothing was written.</span>
            <span className="nb-sp" />
            <button type="button" className="gl-btn" data-size="sm" data-variant="primary" disabled={live} onClick={() => { setStaleKey(null); rerunFrom(cell.id) }}>
              ↻ Rerun from here
            </button>
          </div>
        </div>
      )}
      {applied && (
        <div className="nb-card" data-tone="ok" data-testid="nb-change-applied">
          Applied {plural(Number(o.applied_rows ?? diff.length), 'row')} <ByName name={String(o.applied_by ?? 'unknown')} /> at {fmtTime(String(o.applied_at ?? ''))}
          {o.already_applied ? ' · already applied earlier (idempotent)' : ''}
        </div>
      )}
      {r.status === 'discarded' && <div className="nb-card">Discarded · nothing was written</div>}
      {live && pending && <span className="sr-only">run paused for this change</span>}
    </>
  )
}

/** A cell of a removed kind (approval / person) from an older notebook: read-only; runs skip it; it can be deleted. */
export function RemovedBody({ cell }: { cell: Cell }) {
  const { actions, fail, live } = useNb()
  const o = cell.spec as Record<string, unknown>
  const what = cell.kind === 'approval' ? 'an approval step' : 'a person task'
  return (
    <div className="nb-card" data-tone="idle" data-testid="nb-removed-card">
      <div className="nb-row">
        <b>This step type was removed</b>
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-size="sm" disabled={live || actions.deleteCell.isPending}
          onClick={() => actions.deleteCell.mutate(cell.id, { onError: fail })}>
          Delete step
        </button>
      </div>
      <p className="nb-faint mt-1 mb-0 text-[13px]">
        It was {what}{o.title ? <> (“{String(o.title)}”{o.assignee ? <> for {String(o.assignee)}</> : null})</> : null}. Runs skip it and
        Keep leaves it out. To hold a run for someone, put the write in a Change step: it shows the diff and waits for Apply.
      </p>
    </div>
  )
}

export function WaitBody({ cell }: { cell: Cell }) {
  const { runningId, nb } = useNb()
  const r = cell.latest
  const s = cell.spec as WaitSpec
  const live = nb.runs.find((x) => x.id === nb.active_run_id)
  if (runningId === cell.id && live?.status === 'waiting')
    return (
      <div className="nb-card" data-tone="need" data-testid="nb-wait-card">
        Waiting{s.mode === 'condition' ? ` · checking every ${s.poll_every ?? '30s'}, up to ${s.timeout ?? '1h'}` : ` ${s.duration ?? ''}`}…
      </div>
    )
  if (!r || r.status !== 'success') return null
  const o = out(r)
  return (
    <div className="nb-card" data-tone="ok" data-testid="nb-wait-card">
      Resolved after {String(o.waited_s ?? '?')} s{o.polls ? ` · ${plural(Number(o.polls), 'check')}` : ''}
      <span className="nb-coming ml-2">event waits · coming</span>
    </div>
  )
}

/** The HTTP cell's result and request editor live in ./Http (API testing). */
export const HttpBody = HttpResultBody

export function WorkflowBody({ cell }: { cell: Cell }) {
  const { openWs, nb } = useNb()
  const r = cell.latest
  const spec = cell.spec as WorkflowSpec
  const o = out(r)
  const execId = (o.execution_id as string | undefined) ?? r?.execution_id ?? null
  const exec = useExecution(r ? execId : null)
  const fallbackName = useWorkflowName(spec.workflow_id)
  if (!r) return null
  const run = nb.runs.find((x) => x.id === r.run_id)
  const name = exec?.workflow_name || fallbackName || 'this workflow'
  const att = (exec?.attribution ?? {}) as Record<string, unknown>
  const by = String(att.triggered_by ?? run?.triggered_by ?? '') || null
  const runAs = String(att.run_as ?? run?.run_as ?? '') || null
  const via = String(att.via_agent ?? run?.via_agent ?? '') || null
  const version = typeof spec.version === 'number' ? `v${spec.version} · pinned` : 'published'
  const ok = o.status === 'completed' || o.status === 'success'
  const steps = exec ? Object.keys(exec.node_data ?? {}).length : null
  const kids = exec?.children ?? []
  return (
    <>
      <div className="nb-row mt-1">
        <Chip tone={ok ? 'ok' : r.status === 'error' ? 'prod' : 'neutral'} mono size="sm">
          {String(o.status ?? r.status)}
        </Chip>
        {(runAs || by) && (
          <span className="nb-via" title={via ? `via ${via}` : undefined}>
            <Avatar name={runAs ?? by ?? ''} size="sm" />
            {runAs ? <>as <b>{runAs}</b></> : <>by <b>{by}</b></>}
            {via && <span className="nb-mono">via {via}</span>}
          </span>
        )}
        <span className="nb-ver">{version}</span>
        {o.last_node ? <span className="nb-faint">last step {String(o.last_node)}</span> : null}
        <button type="button" className="nb-linkbtn text-[12.5px]" onClick={() => openWs('workflow', cell.id)}>
          Run ▸
        </button>
      </div>
      {exec && (
        <div className="nb-subwf" data-testid="nb-workflow-run">
          <div className="nb-subwf-h">
            <span className="nb-subwf-ic" aria-hidden>⑂</span>
            <span className="nb-subwf-nm">{name}</span>
            <span className="nb-faint">
              {steps !== null ? plural(steps, 'step') : ''}
              {kids.length ? ` · ${plural(kids.length, 'sub-run')} · ${kids.filter((k) => k.status === 'completed' || k.status === 'success').length} ok` : ''}
              {r.ms ? ` · ${fmtMs(r.ms)}` : ''}
            </span>
            <span className="nb-sp" />
            <StatusPill value={exec.status} />
          </div>
          {kids.length > 0 && (
            <div className="nb-subwf-rows">
              {kids.slice(0, 6).map((k) => (
                <div key={k.execution_id} className="nb-subwf-r">
                  <span className="nb-mono nb-faint truncate">{k.node}</span>
                  <span className="truncate">{k.workflow_name}{k.version_number ? <span className="nb-faint"> · v{k.version_number}</span> : null}</span>
                  <StatusPill value={k.status} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="nb-expl">
        A real workflow run: it appears in {name}&rsquo;s run history{by ? <> with triggered_by <b>{by}</b></> : null}{runAs ? <> as {runAs}</> : null}.
      </div>
    </>
  )
}

interface BStep { index: number; action: string; group?: string | null; status: string; ms?: number | null; error?: string | null }

export function BrowserBody({ cell }: { cell: Cell }) {
  const { openWs } = useNb()
  const r = cell.latest
  const o = out(r)
  const steps = (o.steps as BStep[] | undefined) ?? []
  const failed = steps.some((s) => s.status === 'failed' || s.status === 'blocked')
  const [open, setOpen] = useState<boolean | null>(null)
  const shown = open ?? failed
  return (
    <>
      <div className="nb-row mt-1">
        {r && steps.length > 0 && (
          <Chip tone={failed ? 'prod' : 'ok'} mono size="sm">
            {Number(o.passed ?? 0)} of {Number(o.step_count ?? steps.length)} passed
          </Chip>
        )}
        <button type="button" className="nb-linkbtn text-[12.5px]" onClick={() => openWs('browser', cell.id)}>
          Watch in workspace ▸
        </button>
        <span className="nb-sp" />
        <span className="nb-coming">agent-driven browsing · coming</span>
      </div>
      {steps.length > 0 && (
        <div className="mt-1">
          <button type="button" className="nb-linkbtn text-[12.5px]" aria-expanded={shown} onClick={() => setOpen(!shown)}>
            {shown ? 'Hide steps ▴' : `Steps (${steps.length}) ▾`}
          </button>
          {shown && (
            <div className="nb-steps" data-testid="nb-browser-steps">
              {steps.map((s) => (
                <div key={s.index} data-s={s.status}>
                  <StatusMark size="sm" status={s.status === 'passed' ? 'ok' : s.status === 'failed' || s.status === 'blocked' ? 'fail' : s.status === 'running' ? 'running' : 'pending'} />
                  <span className="nb-ix">{s.index + 1}</span>
                  <span className="min-w-0 truncate">{s.action}{s.group ? ` · ${s.group}` : ''}</span>
                  <span className="nb-sp" style={{ flex: 1 }} />
                  {s.error && <span className="nb-err truncate" style={{ maxWidth: 220 }} title={s.error}>{s.error}</span>}
                  {s.ms ? <span className="nb-faint nb-mono">{fmtMs(s.ms)}</span> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}

const MODES: { value: ForEachMode; label: string; disabled?: boolean; title?: string }[] = [
  { value: 'sequential', label: 'One at a time' },
  { value: 'parallel', label: 'In parallel' },
  { value: 'child_task', label: 'A task per row · coming', disabled: true, title: 'A task per row is coming' },
]

export function ForEachBody({ cell }: { cell: Cell }) {
  const { actions, me, fail, live, rerunFrom, editSpec } = useNb()
  const s = cell.spec as ForEachSpec
  const r = cell.latest
  const o = out(r)
  const rows = ((o.rows as { i: number; status: string; error?: { code?: string; message?: string } | null }[] | undefined) ?? [])
  const total = Number(o.row_count ?? rows.length)
  const ok = Number(o.ok ?? 0)
  const failedN = Number(o.failed ?? 0)
  const retried = (o.retried as number[] | null | undefined) ?? null
  const [all, setAll] = useState(false)
  const [preview, setPreview] = useState<ForEachPreview | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)
  const listed = all ? rows : rows.filter((x) => x.status === 'error')
  const pct = (x: number) => (total > 0 ? `${(x / total) * 100}%` : '0%')
  const loadPreview = async () => {
    setPreviewErr(null)
    try {
      setPreview(await notebookApi.preview(cell.id, 5))
    } catch (e) {
      setPreviewErr(isNotBuilt(e) ? 'Preview is not available on this engine yet.' : errorMessage(e))
    }
  }
  return (
    <div className="mt-2" data-testid="nb-foreach">
      <div className="nb-row">
        <ViewSwitch<ForEachMode>
          size="sm"
          aria-label="How rows run"
          value={s.mode ?? 'sequential'}
          options={MODES}
          onChange={(m) => editSpec(cell, 'mode', m)}
        />
        <span className="nb-faint">max {s.max_rows ?? 500} rows{(s.mode ?? 'sequential') === 'parallel' ? ` · ${s.concurrency ?? 4} at a time` : ' · sequential'}</span>
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-size="sm" onClick={loadPreview}>Preview 5 rows</button>
        <button type="button" className="gl-btn" data-size="sm" data-variant="primary" disabled={live} onClick={() => rerunFrom(cell.id)}>
          {total ? `Run ${plural(total, 'row')}` : 'Run rows'}
        </button>
      </div>
      {previewErr && <div className="nb-faint mt-1">{previewErr}</div>}
      {preview && (
        <div className="nb-card" data-testid="nb-foreach-preview">
          <div className="nb-row"><b>First {preview.rows.length} of {plural(preview.total, 'row')}</b><span className="nb-faint">nothing ran · inputs each body cell would get</span><span className="nb-sp" /><button type="button" className="nb-linkbtn" onClick={() => setPreview(null)}>Close</button></div>
          {preview.error && <div className="nb-err">{preview.error.message}</div>}
          {preview.rows.map((pr) => (
            <div key={pr.i} className="mt-1 nb-mono">
              row {pr.i}: {pr.cells.map((c) => `${c.name} ${c.error ? `✗ ${c.error.message}` : JSON.stringify(c.inputs)}`).join(' · ')}
            </div>
          ))}
        </div>
      )}
      {r && failedN > 0 && (
        <div className="nb-card" data-tone="need" data-testid="nb-foreach-attention">
          <div className="nb-row">
            <span>{failedN} of {plural(total, 'row')} failed · the rows that passed are kept</span>
            <span className="nb-sp" />
            <button
              type="button"
              className="gl-btn"
              data-size="sm"
              data-variant="primary"
              disabled={live || actions.retryRows.isPending}
              onClick={() => actions.retryRows.mutate({ cellId: cell.id, by: me }, { onError: fail })}
            >
              Retry failed rows ({failedN})
            </button>
          </div>
        </div>
      )}
      {r && (
        <div className="gl-group mt-2">
          <div className="gl-group-head">
            <span className="gl-fe-sum">
              <span data-r="ok">{ok} ✓</span>
              <span aria-hidden className="opacity-55">·</span>
              <span data-r={failedN ? 'fail' : undefined}>{failedN} ✗</span>
              {retried && retried.length > 0 && <span className="nb-faint"> · after retrying {plural(retried.length, 'row')}</span>}
            </span>
            <span className="gl-sp" />
            {rows.length > 0 && (
              <button type="button" className="nb-linkbtn text-[12px]" onClick={() => setAll(!all)}>
                {all ? 'Failed rows only' : `All ${plural(total, 'row')}`}
              </button>
            )}
          </div>
          <div className="gl-febar" aria-hidden>
            <i data-r="ok" style={{ width: pct(ok) }} />
            <i data-r="fail" style={{ width: pct(failedN) }} />
          </div>
          {listed.slice(0, 50).map((x) => (
            <div key={x.i} className="gl-ferow" data-status={x.status === 'error' ? 'fail' : 'ok'}>
              <StatusMark size="sm" status={x.status === 'error' ? 'fail' : 'ok'} />
              <span className="gl-ix">row {x.i}</span>
              <span className="min-w-0 truncate">{x.status === 'error' ? x.error?.message ?? 'failed' : 'ok'}</span>
              {x.error?.code && <span className="gl-why">{x.error.code}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** The cells this Ask planned: the names it recorded, else the agent-made cells straight after it. */
function plannedBy(ask: Cell, cells: Cell[]): Cell[] {
  const o = out(ask.latest)
  const names = Array.isArray(o.planned) ? new Set(o.planned as string[]) : null
  const top = cells.filter((c) => !c.parent_cell_id)
  if (names) return top.filter((c) => names.has(c.name))
  const after = top.filter((c) => c.position > ask.position)
  const run: Cell[] = []
  for (const c of after) {
    if (c.created_via !== 'agent' || c.kind === 'ask') break
    run.push(c)
  }
  return run
}

/** "Removed c3, c4 · could not remove c5 (a run is in progress)" from the Ask's `removed` / `not_removed`. */
function removedLine(o: Record<string, unknown>): string | null {
  const gone = Array.isArray(o.removed) ? (o.removed as string[]) : []
  const kept = Array.isArray(o.not_removed) ? (o.not_removed as { name?: string; reason?: string }[]) : []
  const bits = [
    gone.length ? `Removed ${gone.join(', ')}` : null,
    ...kept.map((k) => `could not remove ${k.name ?? '?'}${k.reason ? ` (${k.reason})` : ''}`),
  ].filter(Boolean)
  return bits.length ? bits.join(' · ') : null
}

export function AskBody({ cell }: { cell: Cell }) {
  const { nb, numberOf } = useNb()
  const r = cell.latest
  const o = out(r)
  const planned = plannedBy(cell, nb.cells)
  const skipped = r?.status === 'skipped_ask'
  const noAi = planned.filter((c) => !c.needs_ai).length
  const calls = typeof o.calls === 'number' ? o.calls : null
  const llmMs = typeof o.llm_ms === 'number' ? o.llm_ms : null
  return (
    <>
      {planned.length > 0 && (
        <div className="nb-plan" data-testid="nb-ask-plan">
          <div className="nb-plan-h">
            <span className="nb-plan-logo" aria-hidden />
            <b>Studio</b>
            <span>
              planned {plural(planned.length, 'cell')} · {noAi === planned.length ? 'all' : noAi} run with no AI
            </span>
          </div>
          <ol>
            {planned.map((c) => {
              const st = c.latest && !c.stale ? (c.latest.status === 'error' ? 'fail' : 'ok') : 'pending'
              return (
                <li key={c.id}>
                  <StatusMark size="sm" status={st} />
                  <span className="min-w-0">
                    <span className="nb-plan-n">{numberOf(c.id)}</span>
                    {c.title || KIND_LABEL[c.kind]}
                  </span>
                  {c.needs_ai ? <span className="nb-ab" data-ai="">explore once</span> : <span className="nb-ab">no AI</span>}
                </li>
              )
            })}
          </ol>
        </div>
      )}
      {(planned.length > 0 || skipped) && (
        <div className="nb-expl">
          {[
            calls !== null ? plural(calls, 'model call') : null,
            llmMs !== null ? fmtMs(llmMs) : null,
            'the only cell that needs AI to repeat · editing it means re-planning from here',
          ]
            .filter(Boolean)
            .join(' · ')}
        </div>
      )}
      {removedLine(o) && <div className="nb-faint mt-1" data-testid="nb-ask-removed">{removedLine(o)}</div>}
      {skipped && <div className="nb-faint mt-1">Skipped on rerun · Ask runs once; the cells it wrote run without AI.</div>}
    </>
  )
}

export function StoreBody({ cell }: { cell: Cell }) {
  const r = cell.latest
  if (!r || r.status !== 'success') return null
  const o = out(r)
  return <div className="nb-faint mt-1">{String(o.summary ?? `${plural(Number(o.row_count ?? 0), 'row')} saved`)}</div>
}
