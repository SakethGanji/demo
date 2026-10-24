/** One notebook cell: PageStep chrome · ⋯ menu · guard pill · sentence · result body · attribution line. */
import { useState } from 'react'
import { Menu } from '@base-ui/react/menu'
import { Chip, PageStep, StepResult } from '@/shared/glass'
import { useNb } from '../context'
import { asRows, cellStatus, countOnly, fmtMs, fmtTime, isRef, KIND_LABEL, kindText, plural, tabFor, valueText } from '../lib/cells'
import type { BrowserSpec, Cell, ChangeSpec, CompareOp, ForEachSpec, Guard, HttpSpec, QuerySpec } from '../types'
import { AskBody, BrowserBody, ChangeBody, ErrorCard, ForEachBody, HttpBody, RemovedBody, RowsBody, StoreBody, WaitBody, WorkflowBody } from './Bodies'
import { Avatar } from './Look'
import { isProdEnv } from '../lib/look'
import { Sentence } from './Sentence'

const GUARD_OPS: { v: NonNullable<Guard['op']>; t: string }[] = [
  { v: 'nonempty', t: 'is not empty' }, { v: 'empty', t: 'is empty' }, { v: 'gt', t: '>' }, { v: 'ge', t: '≥' },
  { v: 'eq', t: '=' }, { v: 'ne', t: '≠' }, { v: 'lt', t: '<' }, { v: 'le', t: '≤' },
]
const opText = (op: Guard['op']) => GUARD_OPS.find((o) => o.v === (op ?? 'nonempty'))?.t ?? String(op)

function kindParts(cell: Cell, connName: (id: string) => string): string[] {
  const s = cell.spec as unknown as Record<string, unknown>
  switch (cell.kind) {
    case 'query':
      return [connName((s as unknown as QuerySpec).connection), 'read-only']
    case 'change':
      return [connName((s as unknown as ChangeSpec).connection), 'writes']
    case 'http':
      return [String((s as unknown as HttpSpec).method ?? 'GET')]
    case 'browser': {
      try {
        return [new URL((s as unknown as BrowserSpec).base_url).host]
      } catch {
        return []
      }
    }
    case 'for_each':
      return [String((s as unknown as ForEachSpec).mode ?? 'sequential')]
    case 'ask':
      return cell.created_by ? [cell.created_by] : []
    default:
      return []
  }
}

export function CellView({ cell, n, firstStale, nested }: { cell: Cell; n: string; firstStale: boolean; nested?: React.ReactNode }) {
  const ctx = useNb()
  const { nb, live, runningId, ws, openWs, rerunFrom, actions, fail, collapsed, toggleCollapsed, selected, setSelected, connections } = ctx
  const [guardEdit, setGuardEdit] = useState(false)
  const status = cellStatus(cell, nb, runningId)
  const r = cell.latest
  const run = r ? nb.runs.find((x) => x.id === r.run_id) : undefined
  const connName = (id: string) => connections.find((c) => c.id === id || c.key === id)?.name ?? id
  const guardFalse = r?.status === 'guard_false'
  const linked = ws.open && ws.cellId === cell.id
  const tab = tabFor(cell.kind)
  const isChild = !!cell.parent_cell_id

  const rows = asRows(r?.output ?? null)
  let result: React.ReactNode = null
  if (r && !cell.stale) {
    if (r.status === 'guard_false') result = <StepResult tone="warn">skipped · condition false</StepResult>
    else if (r.status === 'kept_earlier') result = <StepResult>kept from earlier · already applied</StepResult>
    else if (r.status === 'needs_approval') result = <StepResult tone="warn">needs your approval</StepResult>
    else if (r.status === 'waiting') result = <StepResult tone="warn">waiting for {cell.kind === 'wait' ? 'a condition' : 'you'}</StepResult>
    else if (r.status === 'error') result = <StepResult tone="bad">failed · {String(r.error?.code ?? 'error')}</StepResult>
    else if (cell.kind === 'rule' && r.output) {
      const o = r.output as Record<string, unknown>
      const tone = typeof o.mismatches === 'number' && o.mismatches > 0 ? 'warn' : o.passed === false ? 'bad' : 'ok'
      result = <StepResult tone={tone}>{String(o.summary ?? (rows ? plural(rows.row_count, 'row') : 'done'))}</StepResult>
    } else if (rows && cell.kind !== 'for_each' && cell.kind !== 'query' && cell.kind !== 'data') result = <StepResult tone="ok">{plural(rows.row_count, 'row')}{rows.truncated ? ` · capped at ${rows.cap ?? ''}` : ''}</StepResult>
  }

  const who = run ? [run.triggered_by && `by ${run.triggered_by}`, run.run_as && `as ${run.run_as}`, run.via_agent && `via ${run.via_agent}`].filter(Boolean).join(' · ') : ''
  const annotation = r ? (
    <span title={who} className="nb-ann">
      {run?.triggered_by && <Avatar name={run.triggered_by} size="sm" />}
      {[countOnly(cell) !== null ? plural(countOnly(cell)!, 'row') : rows && cell.kind !== 'for_each' ? plural(rows.row_count, 'row') : null, fmtMs(r.ms), who, run ? `run ${run.n}` : null, rows?.truncated ? 'full rows stored with the run' : null].filter(Boolean).join(' · ')}
    </span>
  ) : null

  const badges = (
    <>
      {cell.needs_ai ? (
        <Chip tone="ai" size="sm">needs AI</Chip>
      ) : cell.kind !== 'note' ? (
        <span className="nb-ab" title="replays with no AI">no AI</span>
      ) : null}
      {cell.effect === 'write' && isProdEnv(nb.environment) && <Chip tone="prod" size="sm">writes · PRODUCTION</Chip>}
      {run && <Chip size="sm" mono className="nb-quiet">run {run.n}</Chip>}
      {cell.created_via === 'agent' && (
        <span className="nb-madeby">
          <Avatar name="Studio" size="sm" />
          by Studio
        </span>
      )}
    </>
  )

  const menu = (
    <Menu.Root>
      <Menu.Trigger className="gl-icon-btn" aria-label={`Cell ${n} actions`} data-testid="nb-cell-menu">
        ⋯
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={4} align="end" className="z-[70]">
          <Menu.Popup className="gl-overlay gl-menu">
            {!isChild && (
              <Menu.Item className="gl-menu-item" disabled={live} onClick={() => rerunFrom(cell.id)}>
                <span>Rerun from cell {n}</span>
                <span className="gl-hint">no AI · stops at applied writes</span>
              </Menu.Item>
            )}
            <Menu.Item className="gl-menu-item" disabled={live} onClick={() => setGuardEdit(true)}>
              <span>{cell.guard ? 'Change the condition' : 'Run only if…'}</span>
            </Menu.Item>
            <Menu.Item className="gl-menu-item" onClick={() => toggleCollapsed(cell.id)}>
              <span>{collapsed[cell.id] ? 'Expand' : 'Collapse'}</span>
            </Menu.Item>
            {tab && (
              <Menu.Item className="gl-menu-item" onClick={() => openWs(tab, cell.id)}>
                <span>Open in workspace</span>
              </Menu.Item>
            )}
            <Menu.Item className="gl-menu-item" disabled>
              <span>Fork from cell {n}</span>
              <span className="gl-hint">coming</span>
            </Menu.Item>
            <Menu.Item
              className="gl-menu-item"
              disabled={live || cell.applied}
              onClick={() => actions.deleteCell.mutate(cell.id, { onError: fail })}
            >
              <span>Delete cell</span>
              {cell.applied && <span className="gl-hint">changed data · kept</span>}
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )

  const actionsNode = (
    <>
      {linked ? (
        <span className="nb-pill">Showing in workspace</span>
      ) : null}
      {!firstStale && !live && !isChild && r && (
        <button type="button" className="nb-rr" aria-label={`Rerun from cell ${n}`} onClick={() => rerunFrom(cell.id)}>
          ↻ Rerun from here
        </button>
      )}
      {firstStale && !live && (
        <button type="button" className="gl-btn" data-variant="primary" data-size="sm" onClick={() => rerunFrom(cell.id)} data-testid="nb-rerun-here">
          ↻ Rerun from here
        </button>
      )}
      {menu}
    </>
  )

  return (
    <article
      id={`cell-${cell.id}`}
      className={isChild ? 'nb-child' : 'nb-cell'}
      data-testid="nb-cell"
      data-cell={cell.name}
      data-kind={cell.kind}
      data-status={status}
      data-linked={linked ? '' : undefined}
      data-skipped={guardFalse ? '' : undefined}
      data-collapsed={collapsed[cell.id] ? '' : undefined}
      data-selected={selected === cell.id ? '' : undefined}
    >
      <PageStep
        status={status}
        kind={kindText(cell.kind)}
        kindParts={kindParts(cell, connName)}
        label={cell.title || KIND_LABEL[cell.kind]}
        num={n}
        meta={r ? fmtTime(r.created_at) : undefined}
        badges={badges}
        actions={actionsNode}
        actionsAlways
        stale={cell.stale && r ? `from run ${run?.n ?? r.run_n ?? ''} · edited since` : false}
        selected={selected === cell.id}
        onSelect={() => {
          setSelected(cell.id)
          if (tab && r && ws.open) openWs(tab, cell.id)
        }}
        sentence={<Sentence cell={cell} />}
        result={result}
        annotation={annotation}
        last
      >
        {(cell.guard || guardEdit) && <GuardPill cell={cell} editing={guardEdit} setEditing={setGuardEdit} />}
        {cell.kind === 'query' && (cell.spec as QuerySpec).sql && <SqlFold sql={(cell.spec as QuerySpec).sql} params={(cell.spec as QuerySpec).params} />}
        {r?.error && r.status === 'error' && cell.kind !== 'for_each' && <ErrorCard r={r} />}
        {['query', 'data', 'rule'].includes(cell.kind) && r && !guardFalse && <RowsBody cell={cell} />}
        {cell.kind === 'query' && r && !guardFalse && !cell.needs_ai && (
          <div className="nb-expl">
            Stored as SQL + parameters, so it reruns with no AI{run?.run_as ? <> · reads as <b>{run.run_as}</b></> : null}.
          </div>
        )}
        {cell.kind === 'change' && r && (
          <div className="nb-expl">
            Shows the diff first and writes only on approval; a rerun stops at a write already applied{run?.run_as ? <> · writes as <b>{run.run_as}</b></> : null}
            {nb.environment ? <> · to {nb.environment}</> : null}.
          </div>
        )}
        {cell.kind === 'change' && <ChangeBody cell={cell} />}
        {(cell.removed || cell.kind === 'approval' || cell.kind === 'person') && <RemovedBody cell={cell} />}
        {cell.kind === 'wait' && <WaitBody cell={cell} />}
        {cell.kind === 'http' && <HttpBody cell={cell} />}
        {cell.kind === 'workflow' && <WorkflowBody cell={cell} />}
        {cell.kind === 'browser' && <BrowserBody cell={cell} />}
        {cell.kind === 'ask' && <AskBody cell={cell} />}
        {cell.kind === 'store' && <StoreBody cell={cell} />}
        {cell.kind === 'for_each' && (
          <>
            {r?.error && r.status === 'error' && <ErrorCard r={r} />}
            <ForEachBody cell={cell} />
            <div className="mt-2" data-testid="nb-foreach-body">
              {nested}
              <button type="button" className="nb-linkbtn mt-2 ml-1 text-[12.5px]" disabled={live} onClick={() => ctx.addInside(cell.id)}>
                + Add a step inside
              </button>
            </div>
          </>
        )}
      </PageStep>
    </article>
  )
}

function GuardPill({ cell, editing, setEditing }: { cell: Cell; editing: boolean; setEditing: (v: boolean) => void }) {
  const { actions, fail, nb, live } = useNb()
  const g = cell.guard
  const [left, setLeft] = useState(valueText(g?.left ?? '') ? String(g?.left ?? '') : '')
  const [op, setOp] = useState<NonNullable<Guard['op']>>(g?.op ?? 'nonempty')
  const [right, setRight] = useState(g?.right === undefined || g?.right === null ? '' : String(g.right))
  const r = cell.latest
  const ran = r && !cell.stale && r.status !== 'error'
  const isFalse = r?.status === 'guard_false'
  const suggestions = nb.cells
    .filter((c) => c.position < cell.position && !c.parent_cell_id)
    .flatMap((c) => [`{{cells.${c.name}.count}}`, `{{cells.${c.name}.rows}}`, `{{cells.${c.name}.row_count}}`])
  const save = () => {
    const needsRight = op !== 'nonempty' && op !== 'empty'
    const rv = right.trim() !== '' && !Number.isNaN(Number(right)) ? Number(right) : right
    actions.patchCell.mutate(
      { cellId: cell.id, body: { guard: { left: left.trim(), op, ...(needsRight ? { right: rv } : {}) } } },
      { onError: fail, onSuccess: () => setEditing(false) },
    )
  }
  return (
    <div className="nb-guard" data-false={isFalse ? '' : undefined} data-testid="nb-guard">
      <span className="nb-gi">if</span>
      {g && !editing && (
        <>
          <span>
            Runs only if <span className="vtoken" data-readonly="" title={isRef(g.left) ? g.left : undefined}>{valueText(g.left, nb.cells)}</span> {opText(g.op)}{' '}
            {g.op !== 'nonempty' && g.op !== 'empty' && <span className="vtoken" data-readonly="" title={isRef(g.right) ? g.right : undefined}>{valueText(g.right, nb.cells)}</span>}
          </span>
          {ran && <span className="nb-gres">{isFalse ? 'false · skipped' : 'true this run'}</span>}
          <span className="nb-sp" />
          <button type="button" className="nb-linkbtn text-[12px]" disabled={live} onClick={() => setEditing(true)}>Change</button>
          <button type="button" className="nb-linkbtn text-[12px]" disabled={live} onClick={() => actions.patchCell.mutate({ cellId: cell.id, body: { clear_guard: true } }, { onError: fail })}>
            Remove
          </button>
        </>
      )}
      {editing && (
        <form className="nb-guardform" onSubmit={(e) => (e.preventDefault(), save())}>
          <input className="nb-input" style={{ flex: 1, minWidth: 160 }} list={`g-${cell.id}`} value={left} onChange={(e) => setLeft(e.target.value)} placeholder="{{cells.c3.count}}" aria-label="Condition value" />
          <datalist id={`g-${cell.id}`}>{suggestions.map((s) => <option key={s} value={s} />)}</datalist>
          <select className="nb-select" value={op} onChange={(e) => setOp(e.target.value as CompareOp)} aria-label="Condition operator">
            {GUARD_OPS.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
          </select>
          {op !== 'nonempty' && op !== 'empty' && <input className="nb-input" style={{ width: 90 }} value={right} onChange={(e) => setRight(e.target.value)} aria-label="Compare with" />}
          <button type="submit" className="gl-btn" data-variant="primary" data-size="sm" disabled={!left.trim() || actions.patchCell.isPending}>Save</button>
          <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={() => setEditing(false)}>Cancel</button>
          <span className="nb-faint w-full">Skips this cell unless it holds · becomes an If step when kept</span>
        </form>
      )}
    </div>
  )
}

/** The stored SQL, folded to one row ("SQL stored call · parameters bound ›") until opened. */
function SqlFold({ sql, params }: { sql: string; params?: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false)
  const n = params ? Object.keys(params).length : 0
  return (
    <div className="nb-codeb" data-open={open ? '' : undefined}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        <code>SQL</code> stored call · {n > 0 ? `${plural(n, 'parameter')} bound` : 'parameters bound'}
        <span className="nb-chev" aria-hidden>›</span>
      </button>
      {open && <pre className="nb-sql" aria-label="SQL">{sql}</pre>}
    </div>
  )
}
