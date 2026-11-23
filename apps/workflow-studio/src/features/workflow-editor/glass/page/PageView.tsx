/**
 * The Page view: a workflow read as a numbered document. Structure from `buildPageModel`, words from
 * `pageSentences` / `pageDoc`, the picked run from `runPath` / `pageRun`.
 *
 * Each step is a block: a gutter (marker, number, the green path line), a kind label, the sentence with value
 * tokens, and a sample line from the picked run. A choice lists its cases (If / Is / Otherwise); the case the
 * record took is raised, the others fold to one line. Parallel lanes sit side by side; a loop gets a frame with
 * an item stepper; "If this fails" hangs off the step it guards. Shapes the page cannot say read "This part is
 * wired by hand · Open in Canvas". Nothing is hidden: every node appears once.
 *
 * Embeddable: it takes `{workflow, execution?, readOnly}` and nothing from the editor stores except the shared
 * selection / hover / "show paths not taken" (glassStore).
 */
import { useQuery } from '@tanstack/react-query'
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Chip } from '@/shared/glass/Chip'
import { cx, type StepStatus } from '@/shared/glass/cx'
import { workflowsApi, type ApiExecutionDetail } from '@/shared/lib/api'
import { buildPageModel, caseLabel, parDomId, parKey, stepDomId, toPageWorkflow, type Block, type Case, type PageModel, type PageNode, type PageWorkflow, type Section } from '../lib/pageModel'
import { isTriggerNode, sentenceFor, sentenceParts, type Sentence } from '../lib/pageSentences'
import { caseHead, docKind, docSentence, failRule, humanMs, isSimulated } from '../lib/pageDoc'
import { caseCount, caseTaken, foldText, inLoop, itemOutcome, loopItems, loopRun, passFor, pollSession, reachNote, sampleText, stepForItem, summarize, tryTime } from '../lib/pageRun'
import { formatMs, portTaken, runPath, statusOf, type RunPath } from '../lib/runPath'
import { useGlassStore } from '../state/glassStore'
import { useCheckStore } from '../../stores/checkStore'
import { AddStepMenu, type StepDraft } from './AddStepMenu'
import { Tok, type TokCtx } from './TokenPicker'

export interface PageViewProps {
  workflow: PageWorkflow
  execution?: ApiExecutionDetail | null
  readOnly?: boolean
  /** A token edit: `path` is relative to the node (`parameters.url`, `notes`, `retry_on_fail`). */
  onParamChange?: (node: string, path: string, value: string) => void
  /** "+ Add step" → "Add to workflow": a draft to turn into a real node after `after`. */
  onAddStep?: (draft: StepDraft, after: string | null) => void
  /** Rendered under the trigger step (the editor's Test payload). */
  triggerExtra?: ReactNode
  /** Nested inside another page (a sub-workflow): no DOM ids. */
  nested?: boolean
  /** Model built by the caller (the shell shares one with the rail). */
  model?: PageModel
  /** "Open in Canvas" on a part the page cannot say. */
  onOpenCanvas?: (node: string) => void
  className?: string
}

const CODE_TYPES = new Set(['Code', 'PythonStep'])
const MAIN_KINDS = new Set<Section['kind']>(['intake', 'structure', 'finish', 'steps'])

interface Ctx extends TokCtx {
  nested: boolean
  isOpen: (key: string) => boolean
  toggleCase: (key: string, open: boolean) => void
  selected: string | null
  hover: string | null
  select: (n: string | null) => void
  iter: Record<string, number>
  setIter: (node: string, i: number) => void
  triggerExtra?: ReactNode
  onOpenCanvas?: (node: string) => void
}

/* ── small pieces ─────────────────────────────────────────────────────────────────────────── */

const Check = () => (
  <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M2.5 6.3 5 8.6l4.5-5" />
  </svg>
)
const Clock = () => (
  <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
    <path d="M6 1.5v4l2.5 1.5" />
    <circle cx="6" cy="6" r="4.5" />
  </svg>
)
const Square = () => (
  <svg viewBox="0 0 12 12" aria-hidden>
    <rect x="3" y="3" width="6" height="6" rx="1" fill="currentColor" />
  </svg>
)

function Words({ node, s, ctx }: { node: PageNode; s: Sentence; ctx: Ctx }) {
  return (
    <>
      {sentenceParts(s).map((p, i) =>
        typeof p === 'string' ? <Fragment key={i}>{p}</Fragment> : <Tok key={i} node={node} path={p.token.path} value={p.token.value} label={p.token.label} source={p.token.source} ctx={ctx} />,
      )}
    </>
  )
}

function sentenceOf(node: PageNode): Sentence {
  return docSentence(node) ?? sentenceFor(node)
}

/** The loop a node sits in (nearest), from its folds. */
function loopOf(model: PageModel, node: string): string | undefined {
  const keys = (model.ancestors[node] ?? []).filter((k) => k.endsWith(':loop'))
  const k = keys[keys.length - 1]
  return k ? k.slice(0, -':loop'.length) : undefined
}

function unwrapBody(items: Array<Record<string, unknown>>): unknown[] {
  return items.map((it) => (it && typeof it.body === 'object' && it.body !== null && !Array.isArray(it.body) ? it.body : it))
}

/** The sample line under a step: what this run's record looked like there, or why it did not get there. */
function Sample({ name, ctx }: { name: string; ctx: Ctx }) {
  const path = ctx.path
  if (!path) return null
  const r = path.byNode[name]
  const status = statusOf(path, name)
  if (status === 'fail') return <div className="gp-smp is-bad">Failed{r?.error ? ` · ${r.error.slice(0, 160)}` : ''}</div>
  if (status === 'need') return <div className="gp-smp is-warn">{ctx.model.nodes[name]?.type === 'Approval' ? 'Waiting on a person' : 'Waiting'}</div>
  if (status === 'running') return <div className="gp-smp">Running…</div>
  if (status === 'skip' || status === 'pending') {
    const note = reachNote(path, ctx.model, name)
    const text = !note
      ? 'Not run yet.'
      : note.kind === 'not-taken'
        ? 'Not taken by this record.'
        : note.kind === 'not-reached'
          ? `Not reached: this record stopped at step ${note.num}.`
          : "Didn't run for this record."
    return (
      <div className="gp-smp">
        <span className="gp-nr">{text}</span>
      </div>
    )
  }
  if (!r) return null
  const loop = loopOf(ctx.model, name)
  const history = loop ? loopRun(path, loop) : undefined
  if (loop && history) {
    // the run kept what this step did for every item: show the one being followed
    const n = Math.max(loopItems(path, loop).length, Number(history.totalItems ?? 0) || 0)
    const k = Math.min(ctx.iter[loop] ?? Math.max(0, n - 1), Math.max(0, n - 1))
    const at = stepForItem(path, ctx.model, name, k)
    if (!at)
      return (
        <div className="gp-smp" data-testid="glass-sample">
          <span className="gp-nr">Not kept for item {k + 1}: only the first passes of a long loop are kept.</span>
        </div>
      )
    const st = at.step
    if (!st) {
      const why = at.pass.failedAt && at.pass.failedAt !== name ? ` It failed at “${ctx.model.nodes[at.pass.failedAt]?.name ?? at.pass.failedAt}”.` : ''
      return (
        <div className="gp-smp" data-testid="glass-sample">
          <span className="gp-nr">Not run for item {k + 1}.{why}</span>
        </div>
      )
    }
    if (st.status === 'failed' || st.status === 'stopped')
      return <div className="gp-smp is-bad" data-testid="glass-sample">Failed for item {k + 1}{st.error ? ` · ${st.error.slice(0, 160)}` : ''}</div>
    if (st.status === 'skipped')
      return <div className="gp-smp is-warn" data-testid="glass-sample">Failed for item {k + 1}, skipped{st.error ? ` · ${st.error.slice(0, 160)}` : ''}</div>
    const bits: string[] = []
    if (st.status === 'error_continued' || st.status === 'error_routed') bits.push(`failed${st.error ? `: ${st.error.slice(0, 80)}` : ''}, carried on`)
    if (st.retries) bits.push(`retried ${st.retries}×`)
    const decision = st.branchDecision
    if (decision !== undefined && decision !== null) bits.push(`took “${String(decision)}”`)
    if (st.runs && st.runs > 1) bits.push(`ran ${st.runs}×`)
    return (
      <div className="gp-smp" data-testid="glass-sample">
        <span>{sampleText(st.items ?? [])}</span>
        {bits.length > 0 && <span>· {bits.join(' · ')}</span>}
        {st.ms !== undefined && <span className="gp-ms">{formatMs(st.ms)}</span>}
      </div>
    )
  }
  // a run saved before the engine kept each item: only the last item's data is there
  if (loop) {
    const n = loopItems(path, loop).length
    const k = ctx.iter[loop]
    if (n > 1 && k !== undefined && k !== n - 1)
      return (
        <div className="gp-smp">
          <span className="gp-nr">Kept for the last item only (item {n}). Step through to item {n} to see it.</span>
        </div>
      )
  }
  const isTrig = name === ctx.model.trigger
  const items = isTrig ? unwrapBody(r.items) : r.items
  const bits: string[] = []
  if (r.retries) bits.push(`retried ${r.retries}×`)
  if (r.decision) bits.push(`took “${r.decision}”`)
  return (
    <div className="gp-smp" data-testid="glass-sample">
      <span>{sampleText(items)}</span>
      {bits.length > 0 && <span>· {bits.join(' · ')}</span>}
      {r.ms !== undefined && <span className="gp-ms">{formatMs(r.ms)}</span>}
    </div>
  )
}

const THEN: Array<{ value: string; label: string }> = [
  { value: 'stop', label: 'stop the run' },
  { value: 'continue', label: 'carry on with the next step' },
  { value: 'error_output', label: 'take the “it fails” path' },
]

/** "If this fails": the step's retry and on-error settings, in red, hanging off the step. */
function FailRule({ name, ctx }: { name: string; ctx: Ctx }) {
  const node = ctx.model.nodes[name]
  const rule = failRule(node)
  if (!rule) return null
  const r = ctx.path?.byNode[name]
  const thenRaw = rule.then === 'error' ? 'error_output' : rule.then
  const thenTok = <Tok node={node} path="on_error" raw={thenRaw} value={THEN.find((t) => t.value === thenRaw)!.label} label="after the last try" ctx={ctx} choices={THEN} />
  const times = <Tok node={node} path="retry_on_fail" raw={rule.retries} value={`${rule.retries} time${rule.retries === 1 ? '' : 's'}`} label="tries again" inputType="number" ctx={ctx} />
  const st = !ctx.path
    ? 'Applies to every run'
    : r?.retries
      ? `Used: retried ${r.retries}× in this run`
      : r?.status === 'fail'
        ? 'Used: it failed in this run'
        : r && r.status !== 'skip' && r.status !== 'pending'
          ? 'Not used by this record'
          : 'Not reached by this record'
  return (
    <div className="gp-fail" data-testid="glass-fail-rule">
      <span className="gp-kind">If this fails</span>
      <div>
        {rule.retries > 0 ? (
          <>
            Try again {times}, <Tok node={node} path="retry_delay" raw={rule.delayMs} value={humanMs(rule.delayMs)} label="wait between tries (ms)" inputType="number" ctx={ctx} /> apart. If it still fails, {thenTok}.
          </>
        ) : (
          <>
            Don't try again; {thenTok}.
          </>
        )}
        <div className="gp-st">{st}</div>
      </div>
    </div>
  )
}

/** The top-level step numbers after the one `node` sits in: what a Stop keeps from running. */
function stepsAfter(model: PageModel, node: string): string[] {
  const top = model.top[node]
  const nums = model.outline.filter((o) => o.node && o.num && /^\d+$/.test(o.num)).map((o) => o.num!)
  const i = nums.indexOf(top)
  return i < 0 ? [] : nums.slice(i + 1)
}

function listWords(xs: string[]): string {
  if (xs.length <= 1) return xs.join('')
  return `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`
}

interface BlkProps {
  name: string
  ctx: Ctx
  children?: ReactNode
  end?: boolean
  /** Shown instead of the node's own sentence. */
  sentence?: ReactNode
}

/** One numbered step. */
function Blk({ name, ctx, children, end, sentence }: BlkProps) {
  const node = ctx.model.nodes[name]
  const path = ctx.path
  const status: StepStatus = path ? statusOf(path, name) : node.disabled ? 'skip' : 'pending'
  const on = !!path && (status === 'ok' || status === 'need' || status === 'fail')
  const off = !!path && !on
  const trig = isTriggerNode(node.type)
  const stop = node.type === 'StopAndError'
  const selected = ctx.selected === name || ctx.hover === name
  const nested = !!loopOf(ctx.model, name) && (node.type === 'Loop' || node.type === 'SplitInBatches')
  const after = stop ? stepsAfter(ctx.model, name) : []
  const mk = trig ? <Clock /> : stop ? <Square /> : status === 'fail' ? '!' : status === 'need' ? '…' : on ? <Check /> : null
  return (
    <div
      id={ctx.nested ? undefined : stepDomId(name)}
      className={cx('gp-blk', on && 'on', off && 'off', trig && 'trig', stop && 'stopb', status === 'fail' && 'bad', status === 'need' && 'need', end && 'end', selected && 'sel')}
      data-glass-step=""
      data-testid="glass-step"
      data-node={name}
      data-status={status}
      data-path={path && (status === 'ok' || status === 'need') ? '' : undefined}
      data-dim={off ? '' : undefined}
      data-selected={selected ? '' : undefined}
      onClick={(e) => {
        e.stopPropagation()
        if ((e.target as Element).closest('button, a, input, textarea, select, [data-glass-token-editor]')) return
        ctx.select(name)
      }}
    >
      <div className="gp-g">
        <span className="gp-mk">{mk}</span>
        <span className="gp-num gl-step-num">{ctx.model.numbers[name]}</span>
      </div>
      <div className="gp-bc">
        <div className="gp-bty">
          <span className="gp-kind">{docKind(node, { inLoop: nested })}</span>
          {isSimulated(node) && <span className="gp-chip-sim">simulated in tests</span>}
          {node.disabled && <Chip size="sm">off</Chip>}
          {node.label && node.label !== node.name ? null : null}
        </div>
        <p className="gp-p gl-step-sentence">{sentence ?? <Words node={node} s={sentenceOf(node)} ctx={ctx} />}</p>
        {CODE_TYPES.has(node.type) ? <CodeExpand node={node} /> : null}
        {!CODE_TYPES.has(node.type) && node.notes?.trim() ? <div className="gp-note">{node.notes.trim()}</div> : null}
        {node.type === 'ExecuteWorkflow' && typeof node.parameters.workflowId === 'string' && node.parameters.workflowId ? <SubWorkflow id={node.parameters.workflowId} ctx={ctx} /> : null}
        <Sample name={name} ctx={ctx} />
        <FailRule name={name} ctx={ctx} />
        {stop && <div className="gp-stopline">{after.length ? `Step${after.length === 1 ? '' : 's'} ${listWords(after)} ${after.length === 1 ? "doesn't" : "don't"} run for records on this path.` : 'The run ends here for records on this path.'}</div>}
        {name === ctx.model.trigger ? ctx.triggerExtra : null}
        {children}
      </div>
    </div>
  )
}

function SubWorkflow({ id, ctx }: { id: string; ctx: Ctx }) {
  const [open, setOpen] = useState(false)
  const q = useQuery({ queryKey: ['glass', 'subflow', id], queryFn: () => workflowsApi.get(id), enabled: open, staleTime: 30_000 })
  const wf = useMemo(() => (q.data ? toPageWorkflow({ id: q.data.id, name: q.data.name, nodes: q.data.definition.nodes, connections: q.data.definition.connections }) : null), [q.data])
  return (
    <div className="gp-expand">
      <button type="button" className="gp-expbtn" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid="glass-subflow-toggle">
        {open ? 'Hide' : 'Show'} the steps of {q.data?.name ?? 'this workflow'}
      </button>
      {open && (q.isLoading ? <div className="gp-faint gp-small">Loading…</div> : q.isError ? <div className="gp-faint gp-small">Could not load {id}.</div> : wf && !ctx.nested && <PageView workflow={wf} readOnly nested className="gp-nested" />)}
      {open && ctx.nested && <div className="gp-faint gp-small">Open {q.data?.name ?? id} to read its steps.</div>}
    </div>
  )
}

function CodeExpand({ node }: { node: PageNode }) {
  const [open, setOpen] = useState(false)
  const code = String(node.parameters.code ?? node.parameters.source ?? '')
  if (!code.trim()) return null
  return (
    <div className="gp-expand">
      <button type="button" className="gp-expbtn" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid="glass-code-toggle">
        {open ? 'Hide code' : `Show code · ${code.split('\n').filter((l) => l.trim()).length} lines`}
      </button>
      {open && <pre className="gp-code">{code}</pre>}
    </div>
  )
}

/* ── blocks ───────────────────────────────────────────────────────────────────────────────── */

function blockKey(b: Block, i: number) {
  return `${b.t}-${'node' in b ? b.node : b.t === 'parallel' ? b.from : b.target}-${i}`
}

function Blocks({ blocks, ctx, end = true }: { blocks: Block[]; ctx: Ctx; end?: boolean }) {
  return (
    <>
      {blocks.map((b, i) => (
        <BlockView key={blockKey(b, i)} b={b} ctx={ctx} end={end && i === blocks.length - 1} />
      ))}
    </>
  )
}

function Hand({ target, ctx, text }: { target: string; ctx: Ctx; text: ReactNode }) {
  return (
    <div className="gp-hand" data-testid="glass-hand">
      <span>{text}</span>
      <span className="gp-faint">· This part is wired by hand ·</span>
      <button type="button" className="gp-expbtn" onClick={() => (ctx.onOpenCanvas ? ctx.onOpenCanvas(target) : ctx.select(target))} data-testid="glass-open-canvas">
        Open in Canvas
      </button>
    </div>
  )
}

function CaseView({ branch, c, ctx, showCounts, numbered }: { branch: string; c: Case; ctx: Ctx; showCounts: boolean; numbered?: boolean }) {
  const node = ctx.model.nodes[branch]
  const taken = caseTaken(ctx.path, branch, c)
  const open = ctx.isOpen(c.key)
  const head = caseHead(node, c.port, c.label)
  const count = caseCount(ctx.path, ctx.model, ctx.connections, branch, c)
  const fold = count && count > 1 ? `${count} items went this way` : `${c.blocks.length ? foldText(c.blocks, ctx.model) : 'No steps'}${taken === false ? ' · not taken by this record' : ''}`
  return (
    <div
      className={cx('gl-case gp-case', taken === true && 'on', taken === false && 'off', !open && 'folded', head.tone === 'bad' && 'bad')}
      data-case={c.key}
      data-open={open ? '' : undefined}
      data-taken={taken === true ? '' : undefined}
      data-testid="glass-case"
    >
      <div
        className="gl-case-head gp-ch"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation()
          ctx.toggleCase(c.key, !open)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') (e.preventDefault(), ctx.toggleCase(c.key, !open))
        }}
      >
        <span className="gp-cs">{head.cs}</span>
        <span className="gp-ct">{head.text}</span>
        {showCounts && count !== undefined && (
          <span className="gp-cnt" data-testid="glass-case-count">
            {count} item{count === 1 ? '' : 's'}
          </span>
        )}
        {taken === true && (
          <Chip size="sm" tone="ok" className="gp-tk">
            This record
          </Chip>
        )}
      </div>
      {open ? (
        <div className="gp-cbody">
          <div className="gp-list">
            {c.blocks.length ? (
              <Blocks blocks={c.blocks} ctx={ctx} />
            ) : (
              <div className="gp-line">{c.continues ? <>Goes straight on to step {ctx.model.numbers[c.continues] ?? ''}.</> : 'Nothing more on this path.'}</div>
            )}
          </div>
        </div>
      ) : (
        <div className="gp-fold">
          {fold}
          <button type="button" className="gp-expbtn" onClick={(e) => (e.stopPropagation(), ctx.toggleCase(c.key, true))}>
            Show
          </button>
        </div>
      )}
      {numbered ? null : null}
    </div>
  )
}

function JoinRow({ on, children, ms }: { on: boolean; children: ReactNode; ms?: string }) {
  return (
    <div className="gp-joinrow" data-testid="glass-join">
      <span className={cx('gp-jm', on && 'on')} aria-hidden />
      <span>{children}</span>
      {ms && <span className="gp-ms">{ms}</span>}
    </div>
  )
}

const ran = (path: RunPath | null, n: string | undefined) => {
  const s = n && path?.byNode[n]?.status
  return s === 'ok' || s === 'need' || s === 'fail'
}

function BranchView({ b, ctx, end }: { b: Extract<Block, { t: 'branch' }>; ctx: Ctx; end: boolean }) {
  const node = ctx.model.nodes[b.node]
  const counts = b.cases.map((c) => caseCount(ctx.path, ctx.model, ctx.connections, b.node, c))
  const showCounts = node.type === 'Switch' || counts.some((n) => (n ?? 0) > 1)
  let join: ReactNode = null
  if (b.join) {
    const names = (cs: Case[]) => cs.map((c, i) => <Fragment key={c.key}>{i ? (i === cs.length - 1 ? ' and ' : ', ') : ''}<b>{caseHead(node, c.port, c.label).text}</b></Fragment>)
    const stops = b.cases.filter((c) => summarize(c.blocks, ctx.model).stop)
    const goes = b.cases.filter((c) => !stops.includes(c))
    join = (
      <JoinRow on={ran(ctx.path, b.join)}>
        {stops.length === 0 ? (
          <>Every path continues here, at step {ctx.model.numbers[b.join]}.</>
        ) : (
          <>
            The {names(goes)} path{goes.length === 1 ? '' : 's'} continue{goes.length === 1 ? 's' : ''} here. The {names(stops)} path{stops.length === 1 ? ' has' : 's have'} stopped.
          </>
        )}
      </JoinRow>
    )
  }
  return (
    <Blk name={b.node} ctx={ctx} end={end}>
      <div className="gp-cases">
        {b.cases.map((c) => (
          <CaseView key={c.key} branch={b.node} c={c} ctx={ctx} showCounts={showCounts} />
        ))}
      </div>
      {join}
    </Blk>
  )
}

function ParallelView({ b, ctx, end }: { b: Extract<Block, { t: 'parallel' }>; ctx: Ctx; end: boolean }) {
  const firsts = b.lanes.map((l) => l.find((x) => x.t !== 'goto')).map((x) => (x && 'node' in x ? x.node : undefined))
  const on = !!ctx.path && firsts.some((n) => ran(ctx.path, n))
  const off = !!ctx.path && !on
  const n = b.lanes.length
  const both = n === 2 ? 'both' : `all ${n}`
  const laneMs = (bs: Block[]): number => bs.reduce((a, x) => a + ('node' in x ? (ctx.path?.byNode[x.node]?.ms ?? 0) : x.t === 'parallel' ? Math.max(0, ...x.lanes.map(laneMs)) : 0), 0)
  const ms = ctx.path ? Math.max(0, ...b.lanes.map(laneMs)) : 0
  return (
    <div id={ctx.nested ? undefined : parDomId(b)} className={cx('gp-blk', on && 'on', off && 'off', end && 'end')} data-testid="glass-parallel">
      <div className="gp-g">
        <span className="gp-mk">{on ? <Check /> : null}</span>
        <span className="gp-num gl-step-num">{ctx.model.numbers[parKey(b)]}</span>
      </div>
      <div className="gp-bc">
        <div className="gp-bty">
          <span className="gp-kind">At the same time</span>
        </div>
        <p className="gp-p">Do {both} of these at the same time.</p>
        <div className="gp-par">
          <div className="gp-lh">
            <span className="gp-lanes-ic" aria-hidden>
              {b.lanes.map((_, i) => (
                <i key={i} />
              ))}
            </span>
            {n} paths run together
          </div>
          <div className="gp-lanes" style={{ gridTemplateColumns: `repeat(${Math.min(n, 3)}, minmax(0, 1fr))` }}>
            {b.lanes.map((lane, i) => (
              <div key={i} className="gp-lane gl-lane">
                <div className="gp-list">
                  <Blocks blocks={lane} ctx={ctx} />
                </div>
              </div>
            ))}
          </div>
          <JoinRow on={b.join ? ran(ctx.path, b.join) : on} ms={ms ? formatMs(ms) : undefined}>
            {b.join ? <>Then continue when {both} finish.</> : <>Each path ends on its own.</>}
          </JoinRow>
        </div>
      </div>
    </div>
  )
}

const ON_ITEM_FAILURE: Array<{ value: string; label: string }> = [
  { value: 'stop', label: 'the run stops there' },
  { value: 'skip', label: 'skip it and keep going' },
]
const OUTCOME_CLASS = { ok: 'o-ok', failed: 'o-bad', skipped: 'o-skip', running: 'o-none', none: 'o-none' } as const
const OUTCOME_WORDS = { ok: 'went through', failed: 'failed', skipped: 'failed, skipped', running: 'not finished', none: 'not run' } as const

function LoopView({ b, ctx, end }: { b: Extract<Block, { t: 'loop' }>; ctx: Ctx; end: boolean }) {
  const node = ctx.model.nodes[b.node]
  const inner = inLoop(ctx.model, b.node)
  const doneItems = loopItems(ctx.path, b.node)
  const hist = loopRun(ctx.path, b.node)
  // with the per-item history a loop that stopped part way still has its items (from each pass)
  const n = Math.max(doneItems.length, Number(hist?.totalItems ?? 0) || 0)
  const items: unknown[] = doneItems.length >= n ? doneItems : Array.from({ length: n }, (_, i) => {
    const ps = passFor(hist, i)
    const bs = Math.max(1, Number(hist?.batchSize ?? 1) || 1)
    return ps?.items?.[i % bs]
  })
  const k = Math.min(ctx.iter[b.node] ?? Math.max(0, n - 1), Math.max(0, n - 1))
  const r = ctx.path?.byNode[b.node]
  const done = !!r && r.ports.includes('done')
  const p = node.parameters ?? {}

  if (b.kind === 'until') {
    const timedOut = !!r && r.ports.includes('timeout')
    const outer = loopOf(ctx.model, b.node)
    const session = pollSession(ctx.path, b.node, outer, outer ? ctx.iter[outer] : undefined)
    if (session && session.tries.length > 0) {
      const last = session.tries[session.tries.length - 1]
      const end_ = last.result === 'met' ? 'condition met' : last.result === 'timeout' ? 'timed out' : 'still checking'
      return (
        <Blk name={b.node} ctx={ctx} end={end}>
          <div className="gp-rep" data-testid="glass-repeat-until">
            <span className="gp-kind">
              Tries for this record · {session.attempts ?? session.tries.length} {(session.attempts ?? session.tries.length) === 1 ? 'try' : 'tries'}
            </span>
            <div className="gp-tries" data-testid="glass-poll-tries">
              {session.tries.map((t) => (
                <span key={t.n} className={cx('gp-try', t.result === 'met' && 'done', t.result === 'timeout' && 'bad')} title={t.value !== undefined ? `value: ${(typeof t.value === 'string' ? t.value : JSON.stringify(t.value)).slice(0, 400)}` : undefined} data-testid="glass-poll-try">
                  try {t.n} · {tryTime(t.at)} {t.result === 'met' ? '✓ met' : t.result === 'timeout' ? 'timed out' : 'not yet'}
                  {t.value !== undefined && t.value !== null && t.value !== '' && <span className="gp-faint"> · {sampleText([t.value]).slice(0, 48)}</span>}
                </span>
              ))}
              {session.truncated && <span className="gp-faint gp-small">earlier tries not kept</span>}
              <span className="gp-faint gp-small">{last.result === 'pending' ? 'still checking' : `stopped repeating: ${end_}`}</span>
            </div>
          </div>
          {b.body.length > 0 && (
            <div className="gp-loopf inner">
              <div className="gp-lhead">
                <span className="gp-kind">Each check</span>
              </div>
              <div className="gp-list">
                <Blocks blocks={b.body} ctx={ctx} />
              </div>
            </div>
          )}
          {b.exits.length > 0 && (
            <div className="gp-cases">
              {b.exits.map((c) => (
                <CaseView key={c.key} branch={b.node} c={c} ctx={ctx} showCounts={false} />
              ))}
            </div>
          )}
        </Blk>
      )
    }
    return (
      <Blk name={b.node} ctx={ctx} end={end}>
        <div className="gp-rep" data-testid="glass-repeat-until">
          <span className="gp-kind">Tries for this record</span>
          <div className="gp-tries">
            {!ctx.path ? (
              <span className="gp-try">Tries show after a run</span>
            ) : done ? (
              <span className="gp-try done">✓ condition met</span>
            ) : timedOut ? (
              <span className="gp-try bad">timed out</span>
            ) : r && r.status !== 'skip' && r.status !== 'pending' ? (
              <span className="gp-try">still checking</span>
            ) : (
              <span className="gp-try">not reached</span>
            )}
            {ctx.path && r && r.status !== 'skip' && <span className="gp-faint gp-small">The engine doesn't record each try, only how it ended.</span>}
          </div>
        </div>
        {b.body.length > 0 && (
          <div className="gp-loopf inner">
            <div className="gp-lhead">
              <span className="gp-kind">Each check</span>
            </div>
            <div className="gp-list">
              <Blocks blocks={b.body} ctx={ctx} />
            </div>
          </div>
        )}
        {b.exits.length > 0 && (
          <div className="gp-cases">
            {b.exits.map((c) => (
              <CaseView key={c.key} branch={b.node} c={c} ctx={ctx} showCounts={false} />
            ))}
          </div>
        )}
      </Blk>
    )
  }

  const history = loopRun(ctx.path, b.node)
  const max = Number(p.maxIterations)
  const head = n ? `Repeats ${n} time${n === 1 ? '' : 's'}` : b.kind === 'times' && max > 0 ? `Repeats ${max} times` : 'Repeats once per item'
  const batch = Math.max(1, Number(p.batchSize ?? 1) || 1)
  const bodyNodes: string[] = []
  const walk = (bs: Block[]) => bs.forEach((x) => (x.t === 'parallel' ? x.lanes.forEach(walk) : 'node' in x && (bodyNodes.push(x.node), x.t === 'branch' ? x.cases.forEach((c) => walk(c.blocks)) : x.t === 'loop' ? walk(x.body) : null)))
  walk(b.body)
  const skips = bodyNodes.length > 0 && bodyNodes.every((x) => failRule(ctx.model.nodes[x])?.then === 'continue')
  const shownItems = items.slice(0, 60)
  return (
    <Blk name={b.node} ctx={ctx} end={end}>
      <div className={cx('gp-loopf', inner && 'inner')} data-testid="glass-loop">
        <div className="gp-lhead">
          <span className="gp-kind">{head}</span>
          {n > 1 && (
            <div className="gp-stepper" aria-label="Pick an item to follow" data-testid="glass-loop-stepper">
              <button type="button" aria-label="Previous item" onClick={() => ctx.setIter(b.node, (k - 1 + n) % n)}>
                ‹
              </button>
              <span className="gp-pos">
                item {k + 1} of {n}
              </span>
              <button type="button" aria-label="Next item" onClick={() => ctx.setIter(b.node, (k + 1) % n)}>
                ›
              </button>
            </div>
          )}
        </div>
        <div className="gp-lrules">
          <span>
            Take <Tok node={node} path="parameters.batchSize" raw={p.batchSize ?? 1} value={`${batch} item${batch === 1 ? '' : 's'}`} label="items per pass" inputType="number" ctx={ctx} /> at a time, one pass after another
          </span>
          {p.concurrency !== undefined && p.concurrency !== '' ? (
            <span>
              Run <Tok node={node} path="parameters.concurrency" raw={p.concurrency ?? 1} value={`${Math.max(1, Number(p.concurrency ?? 1) || 1)} at a time`} label="items run at the same time" inputType="number" ctx={ctx} />
              {history?.concurrencyFallback && <span className="gp-faint"> (ran one by one: {history.concurrencyFallback})</span>}
              {history?.concurrencyClamped && <span className="gp-faint" data-testid="glass-loop-clamped"> ({history.concurrencyClamped})</span>}
            </span>
          ) : null}
          <LoopCheckNotes node={b.node} />
          <span>
            If one fails:{' '}
            {p.onItemFailure !== undefined ? (
              <Tok node={node} path="parameters.onItemFailure" raw={p.onItemFailure} value={p.onItemFailure === 'skip' ? 'skip it and keep going' : 'the run stops there'} label="when an item fails" choices={ON_ITEM_FAILURE} ctx={ctx} />
            ) : skips ? (
              'skip it and keep going'
            ) : (
              'the run stops there'
            )}
          </span>
          {Number(p.stopAfterFailures ?? 0) > 0 && (
            <span>
              Stop early after <Tok node={node} path="parameters.stopAfterFailures" raw={p.stopAfterFailures} value={`${Number(p.stopAfterFailures)} failures in a row`} label="failures in a row" inputType="number" ctx={ctx} />
              {history && (
                <span className="gp-faint" data-testid="glass-loop-stop-early">
                  {' '}· {history.stoppedEarly ? 'it did in this run' : "didn't happen in this run"}
                  {history.longestFailStreak ? ` · longest run of failures: ${history.longestFailStreak}` : ''}
                </span>
              )}
            </span>
          )}
        </div>
        {n > 0 && (
          <>
            <div className="gp-strip" aria-label="Each item">
              {shownItems.map((_, i) => {
                const o = history ? itemOutcome(history, i) : null
                const cls = o === null ? (done ? 'o-ok' : 'o-none') : OUTCOME_CLASS[o]
                const ps = history ? passFor(history, i) : undefined
                const title = o === null ? undefined : `Item ${i + 1}: ${OUTCOME_WORDS[o]}${ps?.error ? ` · ${ps.error.slice(0, 120)}` : ''}`
                return (
                  <button key={i} type="button" className={cx(cls, i === k && 'cur')} aria-label={`Item ${i + 1}`} title={title} data-outcome={o ?? undefined} aria-pressed={i === k} onClick={() => ctx.setIter(b.node, i)}>
                    {i + 1}
                  </button>
                )
              })}
              {n > shownItems.length && <span className="gp-faint gp-small">+{n - shownItems.length} more</span>}
            </div>
            {history ? (
              <div className="gp-legend" data-testid="glass-loop-legend">
                {(['ok', 'failed', 'skipped', 'none'] as const).map((o) => {
                  const c = Array.from({ length: n }, (_, i) => itemOutcome(history, i)).filter((x) => x === o || (o === 'none' && x === 'running')).length
                  return c > 0 ? (
                    <span key={o}>
                      <i className={OUTCOME_CLASS[o]} />
                      {c} {OUTCOME_WORDS[o]}
                    </span>
                  ) : null
                })}
                {history.truncated && <span>Only the first {history.passes.length} passes are kept.</span>}
              </div>
            ) : (
              <div className="gp-legend">
                <span>
                  <i className={done ? 'o-ok' : 'o-none'} />
                  {done ? `Went through ${n}` : `${n} items`}
                </span>
                <span>Results per item aren't kept: the steps below show the last item.</span>
              </div>
            )}
            <div className="gp-smp gp-item" data-testid="glass-loop-item">
              <b>Item {k + 1}</b> · {sampleText([items[k]])}
            </div>
          </>
        )}
        <div className="gp-list">
          <Blocks blocks={b.body} ctx={ctx} />
        </div>
        {b.exits.length > 0 && (
          <div className="gp-cases">
            {b.exits.map((c) => (
              <CaseView key={c.key} branch={b.node} c={c} ctx={ctx} showCounts={false} />
            ))}
          </div>
        )}
        <div className="gp-lfoot">{n ? <>After all {n}: <b>one summary of {n} items</b> goes to the next step.</> : <>After the last item, the next step gets one summary of all of them.</>}</div>
      </div>
    </Blk>
  )
}

function BlockView({ b, ctx, end }: { b: Block; ctx: Ctx; end: boolean }) {
  if (b.t === 'goto')
    return (
      <Hand
        target={b.from}
        ctx={ctx}
        text={
          <>
            ↩ Then back to step {ctx.model.numbers[b.target] ?? ''} ({ctx.model.nodes[b.target]?.label || b.target})
          </>
        }
      />
    )
  if (b.t === 'step') return <Blk name={b.node} ctx={ctx} end={end} />
  if (b.t === 'branch') return <BranchView b={b} ctx={ctx} end={end} />
  if (b.t === 'loop') return <LoopView b={b} ctx={ctx} end={end} />
  return <ParallelView b={b} ctx={ctx} end={end} />
}

function lastNode(bs: Block[]): string | null {
  for (let i = bs.length - 1; i >= 0; i--) {
    const b = bs[i]
    if ('node' in b) return b.t === 'branch' && b.join ? b.join : b.node
  }
  return null
}

function SectionView({ s, ctx, end, onAddStep }: { s: Section; ctx: Ctx; end: boolean; onAddStep?: PageViewProps['onAddStep'] }) {
  const [drafts, setDrafts] = useState<StepDraft[]>([])
  const main = MAIN_KINDS.has(s.kind)
  const body = (
    <>
      <Blocks blocks={s.blocks} ctx={ctx} end={end && !drafts.length} />
      {drafts.map((d, k) => (
        <AddStepMenu.Draft
          key={d.id}
          draft={d}
          onChange={(next) => setDrafts((all) => all.map((x, j) => (j === k ? next : x)))}
          onRemove={() => setDrafts((all) => all.filter((_, j) => j !== k))}
          onAdd={
            onAddStep
              ? () => {
                  onAddStep(d, lastNode(s.blocks))
                  setDrafts((all) => all.filter((_, j) => j !== k))
                }
              : undefined
          }
        />
      ))}
      {!ctx.readOnly && onAddStep && <AddStepMenu onPick={(d) => setDrafts((all) => [...all, d])} />}
    </>
  )
  if (main)
    return (
      <div id={ctx.nested ? undefined : s.id} className="gp-sec" data-kind={s.kind} data-testid="glass-section">
        {body}
      </div>
    )
  return (
    <section id={ctx.nested ? undefined : s.id} className="gp-sec gp-sec-x" data-kind={s.kind} data-testid="glass-section">
      <h3 className="gp-sech">{s.title}</h3>
      <div className="gp-list">{body}</div>
    </section>
  )
}

export function PageView({ workflow, execution, readOnly = false, onParamChange, onAddStep, triggerExtra, nested = false, model: given, onOpenCanvas, className }: PageViewProps) {
  const model = useMemo(() => given ?? buildPageModel(workflow), [given, workflow])
  const path = useMemo(() => runPath(workflow.connections, workflow.nodes.map((n) => n.name), execution ?? null), [workflow, execution])
  const selected = useGlassStore((s) => s.selectedNode)
  const hover = useGlassStore((s) => s.hoverNode)
  const select = useGlassStore((s) => s.select)
  const setHover = useGlassStore((s) => s.setHoverNode)
  const showUntakenStore = useGlassStore((s) => s.showUntaken)
  const showUntaken = !nested && showUntakenStore
  const runKey = execution?.id ?? 'live'

  // folds: without a run every case is open; with one, only the cases it took (all of them with "Show paths not taken")
  const allCases = useMemo(() => {
    const keys: string[] = []
    const walk = (bs: Block[]) =>
      bs.forEach((b) => {
        if (b.t === 'branch') b.cases.forEach((c) => (keys.push(c.key), walk(c.blocks)))
        else if (b.t === 'loop') (walk(b.body), b.exits.forEach((c) => (keys.push(c.key), walk(c.blocks))))
        else if (b.t === 'parallel') b.lanes.forEach(walk)
      })
    model.sections.forEach((s) => walk(s.blocks))
    return keys
  }, [model])
  const initialCases = () => {
    if (!path || showUntaken) return new Set(allCases)
    return new Set(allCases.filter((k) => portTaken(path, k.slice(0, k.lastIndexOf(':')), k.slice(k.lastIndexOf(':') + 1)) !== false))
  }
  const [openCases, setOpenCases] = useState<Set<string>>(initialCases)
  const [iter, setIterState] = useState<Record<string, number>>({})
  const lastKey = useRef(`${runKey}|${showUntaken}`)
  const lastModel = useRef(model)
  useEffect(() => {
    const key = `${runKey}|${showUntaken}`
    if (lastKey.current === key && lastModel.current.sections.length === model.sections.length) return
    const runChanged = lastKey.current.split('|')[0] !== runKey
    lastKey.current = key
    lastModel.current = model
    setOpenCases(initialCases())
    if (runChanged) setIterState({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey, model, showUntaken, path])

  // reveal the selected step: open its folds, then scroll it into view
  useEffect(() => {
    if (!selected || nested) return
    const anc = model.ancestors[selected]
    if (!anc) return
    const cases = anc.slice(1).filter((k) => !k.endsWith(':loop'))
    setOpenCases((o) => (cases.some((c) => !o.has(c)) ? new Set([...o, ...cases]) : o))
    const id = requestAnimationFrame(() => document.getElementById(stepDomId(selected))?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    return () => cancelAnimationFrame(id)
  }, [selected, model, nested])

  const ctx: Ctx = {
    model,
    path,
    connections: workflow.connections,
    readOnly,
    nested,
    onParamChange,
    isOpen: (k) => openCases.has(k),
    toggleCase: (key, open) =>
      setOpenCases((o) => {
        if (o.has(key) === open) return o
        const n = new Set(o)
        if (open) n.add(key)
        else n.delete(key)
        return n
      }),
    selected,
    hover,
    select: (n) => select(n),
    iter,
    setIter: (node, i) => setIterState((s) => ({ ...s, [node]: i })),
    triggerExtra,
    onOpenCanvas,
  }

  // hover linking (Side by side): steps carry ids, map them back to names
  const byDomId = useMemo(() => Object.fromEntries(model.order.map((n) => [stepDomId(n), n])), [model])
  const onOver = (e: React.MouseEvent) => {
    if (nested) return
    const el = (e.target as Element).closest('[data-glass-step]')
    const name = el?.id ? byDomId[el.id] : undefined
    if ((name ?? null) !== hover) setHover(name ?? null)
  }

  if (!workflow.nodes.length) {
    return (
      <div className={cx('gp-page', className)} data-testid="glass-page">
        <div className="gp-empty">This workflow has no steps yet. Add the first one below, or switch to Canvas.</div>
        {!readOnly && onAddStep && <SectionView s={{ id: 'sec-steps', title: 'Steps', kind: 'steps', blocks: [], nodes: [] }} ctx={ctx} end onAddStep={onAddStep} />}
      </div>
    )
  }

  const mains = model.sections.filter((s) => MAIN_KINDS.has(s.kind))
  const lastMain = mains[mains.length - 1]
  return (
    <div className={cx('gp-page', className)} data-testid={nested ? 'glass-subpage' : 'glass-page'} data-run={execution?.id} onMouseOver={onOver} onMouseLeave={() => !nested && hover && setHover(null)}>
      <div className="gp-list gp-main-list">
        {model.trigger && (
          <div id={nested ? undefined : 'trigger'} className="gp-sec" data-kind="trigger-head">
            <BlockView b={{ t: 'step', node: model.trigger }} ctx={ctx} end={!mains.length} />
          </div>
        )}
        {mains.map((s) => (
          <SectionView key={s.id} s={s} ctx={ctx} end={s === lastMain} onAddStep={onAddStep} />
        ))}
      </div>
      {model.sections
        .filter((s) => !MAIN_KINDS.has(s.kind))
        .map((s) => (
          <SectionView key={s.id} s={s} ctx={ctx} end onAddStep={s.kind === 'failure' || s.kind === 'trigger' ? undefined : onAddStep} />
        ))}
    </div>
  )
}

export { caseLabel }
export default PageView


/** The workflow check's warnings about this Loop's "Run at a time" (WF030 bad value, WF031 stop + side by side + calls out). */
function LoopCheckNotes({ node }: { node: string }) {
  const findings = useCheckStore((st) => st.findingsByNodeMap[node])
  const notes = (findings ?? []).filter((f) => f.severity !== 'info' && f.param === 'concurrency')
  if (!notes.length) return null
  return (
    <>
      {notes.map((f) => (
        <span key={`${f.code}:${f.message}`} className="gp-faint" data-testid="glass-loop-check" data-code={f.code} data-severity={f.severity}>
          {f.severity === 'error' ? '!' : '?'} {f.message}
        </span>
      ))}
    </>
  )
}
