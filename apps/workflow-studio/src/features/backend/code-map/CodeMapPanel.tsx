import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useUpdateNodeInternals,
  type Node,
  type NodeProps,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Button } from '@/shared/components/ui/button'
import { MethodChip } from '@/shared/components/api/MethodBadge'
import { arrange, filterFunctions, neighborhood } from './layout'
import { useCodeMap } from './useCodeMap'
import type { Direction, Kind, MapFunction } from './types'
import { CallEdge } from './CallEdge'
import './code-map.css'

type FunctionData = {
  fn: MapFunction
  expanded: boolean
  active: boolean
  muted: boolean
  pinned: boolean
  synced: boolean
  height: number
  onToggle: (id: string) => void
  onPin: (id: string) => void
  onJump: (line: number) => void
  onPreview: (id: string | null) => void
}
type FunctionNode = Node<FunctionData, 'function'>
const FunctionBox = memo(function FunctionBox({ data }: NodeProps<FunctionNode>) {
  const { fn } = data
  const updateInternals = useUpdateNodeInternals()
  useEffect(() => {
    updateInternals(fn.id)
  }, [fn.id, fn.calls, data.expanded, data.height, updateInternals])
  return (
    <div
      className={`code-map-function ${data.active ? 'is-active' : ''} ${data.muted ? 'is-muted' : ''}`}
      style={{ height: data.height }}
      data-testid={`map-function-${fn.id}`}
      onMouseDownCapture={(event) => {
        const button = (event.target as HTMLElement).closest('button')
        if (button) {
          // Native focus scrolling fights the graph's transform-based camera.
          event.preventDefault()
          button.focus({ preventScroll: true })
        }
      }}
      onFocus={() => data.onPreview(fn.id)}
      onBlur={() => data.onPreview(null)}
    >
      <Handle type="target" position={Position.Left} id="definition" style={{ top: 28 }} />
      <div className="code-map-function-head">
        <button className="nodrag nopan map-name" onClick={() => data.onToggle(fn.id)} aria-expanded={data.expanded}>
          <span className="map-f-icon">ƒ</span>
          <span>
            <strong>{fn.label}</strong>
            <code>
              {fn.name}({fn.parameters.join(', ')})
            </code>
          </span>
          <span>{data.expanded ? '−' : '+'}</span>
        </button>
        <button
          className="nodrag nopan map-pin"
          aria-label={`Pin ${fn.label}`}
          aria-pressed={data.pinned}
          onClick={() => data.onPin(fn.id)}
        >
          ⌖
        </button>
      </div>
      <div className="map-routes">
        {fn.routes.length ? (
          fn.routes.map((route) => (
            <span key={route.method + route.path} className="map-route">
              <MethodChip method={route.method} size="sm" />
              {route.path}
            </span>
          ))
        ) : (
          <span>Shared function · single definition</span>
        )}
      </div>
      <div className="map-function-meta">
        L{fn.line}–{fn.end_line} · {fn.calls.length} call sites · {fn.controls.length} control blocks
      </div>
      {!data.expanded &&
        fn.calls
          .filter((c) => c.target)
          .map((call) => <Handle key={call.id} id={call.id} type="source" position={Position.Right} style={{ top: 28 }} />)}
      {data.expanded && (
        <div className="map-function-body">
          <div className="map-body-label">Nested call references</div>
          {fn.calls.map((call) => (
            <div key={call.id} className={`map-call ${call.kind}`}>
              <span>{call.kind === 'connector' ? '▤' : call.kind === 'custom' ? '?' : 'ƒ'}</span>
              <button className="nodrag nopan" disabled={!data.synced} onClick={() => data.onJump(call.line)}>
                <strong>{call.target ? `${call.target}()` : call.label}</strong>
                <small>
                  {call.kind === 'custom'
                    ? 'Unresolved behavior'
                    : call.kind === 'connector'
                      ? 'SDK connector · effects not established'
                      : 'Local function reference'}
                </small>
              </button>
              <code>L{call.line}</code>
              {call.target && <Handle id={call.id} type="source" position={Position.Right} />}
            </div>
          ))}
          {!fn.calls.length && <p>No supported call sites. Open source for the implementation.</p>}
          {!!fn.secret_references.length && (
            <div className="map-secret-refs">
              {fn.secret_references.map((name) => (
                <span key={name}>◇ {name}</span>
              ))}
            </div>
          )}
          <p>{fn.other_call_count} other builtin/method calls · relationships do not imply execution order.</p>
        </div>
      )}
    </div>
  )
})
const ModuleBox = memo(function ModuleBox({ data }: NodeProps) {
  return (
    <div className="code-map-module">
      <strong>{String(data.name)}.py</strong>
      <span>Module boundary · source-derived references</span>
    </div>
  )
})
const nodeTypes = { function: FunctionBox, module: ModuleBox }
const edgeTypes = { call: CallEdge }

export interface CodeMapPanelProps {
  pid: string
  moduleId: string | null
  moduleName: string
  source: string
  enabled: boolean
  onJump: (line: number) => void
}
export function CodeMapPanel(props: CodeMapPanelProps) {
  return (
    <ReactFlowProvider>
      <MapContent key={`${props.pid}:${props.moduleName}`} {...props} />
    </ReactFlowProvider>
  )
}
function MapContent({ pid, moduleId, moduleName, source, enabled, onJump }: CodeMapPanelProps) {
  const analysis = useCodeMap(pid, moduleId, source, enabled)
  const { model, synced } = analysis
  const [selected, setSelected] = useState<string | null>(null),
    [pinned, setPinned] = useState<string | null>(null),
    [hovered, setHovered] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set()),
    [focus, setFocus] = useState(false)
  const [direction, setDirection] = useState<Direction>('both'),
    [depth, setDepth] = useState(1),
    [search, setSearch] = useState(''),
    [kind, setKind] = useState<Kind>('all')
  const [sorted, setSorted] = useState(true),
    [layoutVersion, setLayoutVersion] = useState(0)
  const flow = useReactFlow()
  const canvasRef = useRef<HTMLDivElement>(null)
  const [fittedLayout, setFittedLayout] = useState<ReturnType<typeof arrange> | null>(null)
  const currentId = useMemo(() => model?.functions.find((fn) => fn.id === selected)?.id ?? model?.functions[0]?.id ?? '', [model, selected])
  const current = model?.functions.find((fn) => fn.id === currentId)
  const pin = useCallback((id: string) => {
    setSelected(id)
    setPinned((previous) => (previous === id ? null : id))
  }, [])
  const toggle = useCallback((id: string) => {
    setSelected(id)
    setExpanded((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  const highlight = pinned && model?.functions.some((fn) => fn.id === pinned) ? pinned : hovered
  const related = useMemo(
    () => (model && highlight ? neighborhood(model, highlight, direction, depth) : null),
    [model, highlight, direction, depth],
  )
  const scope = useMemo(
    () => (model && focus ? neighborhood(model, currentId, direction, depth) : null),
    [model, focus, currentId, direction, depth],
  )
  const visible = useMemo(() => (model ? filterFunctions(model, scope, search, kind) : []), [model, scope, search, kind])
  const heights = useMemo(
    () =>
      new Map(
        visible.map((fn) => [
          fn.id,
          expanded.has(fn.id)
            ? 195 + Math.max(1, fn.calls.length) * 58 + (fn.secret_references.length ? Math.ceil(fn.secret_references.length / 2) * 27 : 0)
            : 120,
        ]),
      ),
    [visible, expanded],
  )
  const layout = useMemo(() => arrange(visible, model?.edges ?? [], heights, sorted), [visible, model, heights, sorted])
  const jump = useCallback(
    (line: number) => {
      if (synced) onJump(line)
    },
    [synced, onJump],
  )
  const nodes = useMemo<Node[]>(
    () => [
      {
        id: '~module',
        type: 'module',
        data: { name: moduleName },
        position: { x: 0, y: 0 },
        width: layout.width,
        height: layout.height,
        style: { width: layout.width, height: layout.height },
        selectable: false,
        draggable: false,
      },
      ...visible.map((fn) => ({
        id: fn.id,
        type: 'function',
        parentId: '~module',
        position: layout.positions.get(fn.id)!,
        width: 320,
        height: heights.get(fn.id),
        data: {
          fn,
          expanded: expanded.has(fn.id),
          active: (highlight || currentId) === fn.id,
          muted: !!related && !related.has(fn.id),
          pinned: pinned === fn.id,
          synced,
          height: heights.get(fn.id)!,
          onToggle: toggle,
          onPin: pin,
          onJump: jump,
          onPreview: setHovered,
        },
      })),
    ],
    [visible, moduleName, layout, heights, expanded, highlight, currentId, related, pinned, synced, toggle, pin, jump],
  )
  const edges = useMemo<Edge[]>(() => {
    const ids = new Set(visible.map((fn) => fn.id))
    return (model?.edges ?? [])
      .filter((e) => ids.has(e.from) && ids.has(e.to))
      .map((edge, index) => {
        const lit = !!related && related.has(edge.from) && related.has(edge.to)
        return {
          id: edge.call_id,
          source: edge.from,
          target: edge.to,
          sourceHandle: edge.call_id,
          targetHandle: 'definition',
          type: 'call',
          markerEnd: { type: MarkerType.ArrowClosed, color: lit ? 'var(--primary)' : 'var(--art-line)' },
          label: lit ? `L${edge.line}` : undefined,
          style: {
            stroke: lit ? 'var(--primary)' : 'var(--art-line)',
            strokeWidth: lit ? 2 : 1.4,
            opacity: related && !lit ? 0.2 : 1,
            filter: lit ? 'drop-shadow(0 0 4px var(--glow))' : undefined,
          },
          labelStyle: { fontSize: 10 },
          zIndex: 2,
          data: {
            line: edge.line,
            lane: 65 + (index % 7) * 5,
            sourceRight: layout.positions.get(edge.from)!.x + 320,
            targetLeft: layout.positions.get(edge.to)!.x,
          },
        }
      })
  }, [model, visible, related, layout])
  // Geometry is explicit for this read-only projection. Set the camera directly:
  // React Flow's queued fitView expects externally managed node-change updates.
  const fitGraph = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !flow.viewportInitialized) return
    const zoom = Math.max(0.08, Math.min(1.2, (canvas.clientWidth - 70) / layout.width, (canvas.clientHeight - 90) / layout.height))
    void flow
      .setViewport(
        { x: (canvas.clientWidth - layout.width * zoom) / 2, y: (canvas.clientHeight - layout.height * zoom) / 2, zoom },
        { duration: 0 },
      )
      .then(() => setFittedLayout(layout))
  }, [flow, layout])
  useEffect(() => {
    if (!enabled || !flow.viewportInitialized) return
    const observer = new ResizeObserver(fitGraph)
    if (canvasRef.current) observer.observe(canvasRef.current)
    fitGraph()
    return () => observer.disconnect()
  }, [enabled, fitGraph, layoutVersion, flow.viewportInitialized])
  const reset = () => {
    setSearch('')
    setKind('all')
    setDirection('both')
    setDepth(1)
  }
  return (
    <section className="product-code-map" data-testid="product-code-map">
      <div className="map-toolbar">
        <Button
          variant="outline"
          size="xs"
          onClick={() => {
            setSorted(true)
            setLayoutVersion((v) => v + 1)
          }}
          data-testid="map-auto-layout"
        >
          ↔ Auto layout
        </Button>
        <Button variant="outline" size="xs" onClick={() => setFocus((v) => !v)} data-testid="map-focus">
          {focus ? 'Show all functions' : 'Focus selection'}
        </Button>
        <Button
          variant="ghost"
          size="xs"
          onClick={() => setExpanded(new Set(model?.functions.map((fn) => fn.id)))}
          data-testid="map-expand"
        >
          Expand all
        </Button>
        <Button variant="ghost" size="xs" onClick={() => setExpanded(new Set())} data-testid="map-collapse">
          Collapse all
        </Button>
        <label>
          Explore
          <select aria-label="Map direction" value={direction} onChange={(e) => setDirection(e.target.value as Direction)}>
            <option value="both">Callers + dependencies</option>
            <option value="callers">Callers only</option>
            <option value="dependencies">Dependencies only</option>
          </select>
        </label>
        <label>
          Depth
          <select aria-label="Map depth" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            <option value="1">1 hop</option>
            <option value="2">2 hops</option>
          </select>
        </label>
        <select aria-label="Map function type" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          <option value="all">All function types</option>
          <option value="endpoint">API endpoints</option>
          <option value="connector">Connectors</option>
          <option value="unresolved">Unresolved calls</option>
          <option value="secret">Secret references</option>
        </select>
        <input
          aria-label="Search map functions"
          placeholder="Search functions…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Button variant="ghost" size="xs" onClick={reset}>
          Reset filters
        </Button>
        {pinned && (
          <Button variant="outline" size="xs" onClick={() => setPinned(null)}>
            Clear pin
          </Button>
        )}
      </div>
      <div className={`map-sync ${synced ? '' : 'map-sync-stale'}`} role="status" data-testid="map-sync">
        {synced
          ? 'Synced with current code'
          : analysis.error
            ? `${analysis.state === 'invalid' ? 'Invalid draft' : 'Analysis failed'} · ${analysis.error}`
            : 'Analyzing current draft…'}
        {!synced && model && <b>LAST VALID VIEW · source links disabled</b>}
        {analysis.state === 'failed' && <button onClick={analysis.retry}>Retry analysis</button>}
        <span>
          {model
            ? `${visible.length} of ${model.functions.length} functions · ${model.functions.length - visible.length} hidden · revision ${model.source_hash.slice(0, 10)}`
            : 'Source analysis only'}
        </span>
      </div>
      <div className="map-workspace">
        <div
          ref={canvasRef}
          className={`map-canvas ${!synced ? 'is-stale' : ''}`}
          data-layout-ready={fittedLayout === layout}
          data-testid="map-canvas"
          data-scroll-x="allowed"
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            autoPanOnNodeFocus={false}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            minZoom={0.08}
            maxZoom={2}
            onNodeMouseEnter={(_, node) => {
              if (node.type === 'function') setHovered(node.id)
            }}
            onNodeMouseLeave={() => setHovered(null)}
            onEdgeClick={(_, edge) => {
              if (synced) onJump(edge.data?.line as number)
            }}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={22} size={1} color="var(--art-line)" />
            <Controls showInteractive={false} showFitView={false} />
          </ReactFlow>
          <button className="map-fit-button" onClick={fitGraph}>
            Fit graph
          </button>
          <div className="map-highlight" data-testid="map-highlight">
            {highlight
              ? `${pinned ? 'Pinned' : 'Preview'}: ${highlight} · ${related?.size ?? 0} related functions`
              : 'Hover to trace · pin to keep · drag background to pan'}
          </div>
          {model && !visible.length && (
            <div className="map-empty">
              {model.functions.length ? 'No functions match these filters.' : 'No top-level functions in this module.'}
              <button onClick={reset}>Reset filters</button>
            </div>
          )}
          {!model && (
            <div className="map-empty">
              {analysis.error ? 'Code map is unavailable. Your code is still editable.' : 'Analyzing module structure…'}
            </div>
          )}
        </div>
        {current && (
          <aside className="map-inspector" data-testid="map-inspector">
            <small>Source-linked inspection</small>
            <h3>{current.label}</h3>
            <code>
              {current.name}({current.parameters.join(', ')})
            </code>
            <p>
              {current.routes.length ? 'API operation' : 'Shared function'} · L{current.line}–{current.end_line}
            </p>
            <Button variant="outline" size="xs" disabled={!synced} onClick={() => jump(current.line)} data-testid="map-open-source">
              Open exact source
            </Button>
            <h4>Calls</h4>
            {current.calls
              .filter((c) => c.target)
              .map((call) => (
                <button
                  key={call.id}
                  onClick={() => {
                    setSelected(call.target)
                    setPinned(null)
                    setExpanded(new Set([call.target!]))
                  }}
                >
                  {call.target}
                  <code>L{call.line} ↗</code>
                </button>
              ))}
            <h4>Called by</h4>
            {model?.edges
              .filter((e) => e.to === currentId)
              .map((e) => (
                <button
                  key={e.call_id}
                  onClick={() => {
                    setSelected(e.from)
                    setPinned(null)
                  }}
                >
                  {e.from}
                  <code>L{e.line} ↗</code>
                </button>
              ))}
            {!!current.controls.length && (
              <>
                <h4>Control blocks</h4>
                {current.controls.map((c, i) => (
                  <button key={`${c.line}:${i}`} disabled={!synced} onClick={() => jump(c.line)}>
                    {c.kind}
                    <code>L{c.line} ↗</code>
                  </button>
                ))}
              </>
            )}
            {!!current.secret_references.length && (
              <>
                <h4>Secret names only</h4>
                {current.secret_references.map((name) => (
                  <p key={name}>◇ {name}</p>
                ))}
              </>
            )}
            {!!current.custom_scopes.length && (
              <>
                <h4>Unmapped nested scopes</h4>
                {current.custom_scopes.map((scope, i) => (
                  <button key={`${scope.line}:${i}`} disabled={!synced} onClick={() => jump(scope.line)}>
                    {scope.kind}: {scope.name}
                    <code>L{scope.line} ↗</code>
                  </button>
                ))}
              </>
            )}
            <p className="map-caveat">
              Single-module syntax projection. Arrows show call references, not execution order. Dynamic dispatch and external effects may
              remain unresolved.
            </p>
          </aside>
        )}
      </div>
    </section>
  )
}
