/**
 * Read view: the open workflow laid out top to bottom (see lib/readView), drawn with the canvas's own node
 * cards, icons and tokens. Read-only: it scrolls like a page (pinch or the toolbar to zoom), and a click on a card opens that step's details, the same
 * panel a double-click on the canvas opens. Its own React Flow instance, so the editing canvas underneath keeps
 * its viewport.
 */
import { createElement, memo, useEffect, useMemo, useRef } from 'react'
import {
  Background,
  BackgroundVariant,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useViewport,
  type EdgeProps,
  type NodeProps,
  type Node,
  type Edge,
} from '@xyflow/react'
import { Maximize, Minus, Plus } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Tip } from '@/shared/components/ui/tooltip'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useNDVStore } from '../../stores/ndvStore'
import { getIconForNode } from '../../lib/nodeIcons'
import { normalizeNodeGroup } from '../../lib/nodeConfig'
import { getNodeStyles } from '../../lib/nodeStyles'
import { READ_NODE, layoutRead, readSummary, type ReadEdgeData, type ReadNodeData } from '../../lib/readView'
import './readView.css'

const FIT = { padding: 0.12, maxZoom: 1 }
const PRO_OPTIONS = { hideAttribution: true }

const ReadNode = memo(function ReadNode({ data }: NodeProps<Node<ReadNodeData>>) {
  const group = normalizeNodeGroup(data.group ? [data.group] : undefined)
  const styles = getNodeStyles(group)
  return (
    <div
      className={`rv-card flex items-center gap-2.5 px-3 ${data.disabled ? 'opacity-45' : ''}`}
      style={{ width: READ_NODE.width, height: READ_NODE.height, backgroundColor: styles.bgColor }}
      title={data.detail ? `${data.name}: ${data.detail}` : data.name}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} className="rv-handle" />
      <div
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full shadow-raise-sm ${group === 'ai' ? 'brand-fill' : ''}`}
        style={group === 'ai' ? undefined : { backgroundColor: styles.iconBgColor, color: styles.iconFgColor }}
      >
        {createElement(getIconForNode(data.icon, data.type), { size: 17 })}
      </div>
      <div className="min-w-0 flex-1 leading-tight">
        <div className={`truncate text-[13px] font-medium text-foreground ${data.disabled ? 'line-through' : ''}`}>{data.name}</div>
        {data.detail && <div className="mt-0.5 truncate text-[11.5px] text-muted-foreground">{data.detail}</div>}
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} className="rv-handle" />
    </div>
  )
})

/** A polyline with rounded corners. */
function roundedPath(pts: { x: number; y: number }[], r = 14): string {
  if (pts.length < 2) return ''
  const out = [`M ${pts[0].x} ${pts[0].y}`]
  for (let i = 1; i < pts.length - 1; i++) {
    const [a, b, c] = [pts[i - 1], pts[i], pts[i + 1]]
    const d1 = Math.hypot(b.x - a.x, b.y - a.y)
    const d2 = Math.hypot(c.x - b.x, c.y - b.y)
    if (!d1 || !d2) continue
    const k = Math.min(r, d1 / 2, d2 / 2)
    out.push(`L ${b.x - ((b.x - a.x) / d1) * k} ${b.y - ((b.y - a.y) / d1) * k}`)
    out.push(`Q ${b.x} ${b.y} ${b.x + ((c.x - b.x) / d2) * k} ${b.y + ((c.y - b.y) / d2) * k}`)
  }
  const last = pts[pts.length - 1]
  out.push(`L ${last.x} ${last.y}`)
  return out.join(' ')
}

/** The route the layout chose, with its words (approved, no answer, yes, …) in a chip where the layout made room. */
const ReadEdge = memo(function ReadEdge({ id, data, sourceX, sourceY, targetX, targetY }: EdgeProps<Edge<ReadEdgeData>>) {
  // Ends on the cards' bottom and top middles; the layout's bends in between.
  const inner = (data?.points ?? []).slice(1, -1)
  const d = roundedPath([{ x: sourceX, y: sourceY }, ...inner, { x: targetX, y: targetY - 1 }])
  return (
    <>
      <path id={id} d={d} className="rv-edge" markerEnd="url(#rv-arrow)" />
      {data?.label && data.labelAt && (
        <EdgeLabelRenderer>
          <div
            className="rv-label pointer-events-none absolute rounded-chip px-1.5 py-0.5 text-[11px] font-medium"
            style={{ transform: `translate(-50%, -50%) translate(${data.labelAt.x}px, ${data.labelAt.y}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
})

const nodeTypes = { readNode: ReadNode }
const edgeTypes = { readEdge: ReadEdge }

/** Bottom-centre toolbar, the canvas's own shape: what's in the workflow, then zoom and fit. */
function Toolbar({ summary }: { summary: string[] }) {
  const { zoomIn, zoomOut, zoomTo, fitView } = useReactFlow()
  const { zoom } = useViewport()
  const btn = 'text-muted-foreground hover:text-foreground'
  return (
    <div role="toolbar" aria-label="Read view tools" className="glass-overlay absolute bottom-3 left-1/2 z-10 flex max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-0.5 p-1">
      {summary.length > 0 && (
        <>
          <span className="truncate px-2 text-[12px] text-muted-foreground" data-testid="read-summary">{summary.join(' · ')}</span>
          <span className="mx-1 h-4 w-px shrink-0 self-center bg-hairline" aria-hidden />
        </>
      )}
      <Tip label="Zoom out" side="top">
        <Button variant="ghost" size="icon-sm" className={btn} aria-label="Zoom out" onClick={() => zoomOut()}><Minus /></Button>
      </Tip>
      <Tip label="Reset to 100%" side="top">
        <button type="button" onClick={() => zoomTo(1)} className="h-7 min-w-12 rounded-ctl px-1 text-[12px] font-medium text-foreground tabular-nums outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus">
          {Math.round(zoom * 100)}%
        </button>
      </Tip>
      <Tip label="Zoom in" side="top">
        <Button variant="ghost" size="icon-sm" className={btn} aria-label="Zoom in" onClick={() => zoomIn()}><Plus /></Button>
      </Tip>
      <Tip label="Fit to view" side="top">
        <Button variant="ghost" size="icon-sm" className={btn} aria-label="Fit to view" onClick={() => fitView({ ...FIT, duration: 200 })}><Maximize /></Button>
      </Tip>
    </div>
  )
}

function ReadFlow({ rightInset }: { rightInset: number }) {
  const nodes = useWorkflowStore((s) => s.nodes)
  const edges = useWorkflowStore((s) => s.edges)
  const openNDV = useNDVStore((s) => s.openNDV)
  const layout = useMemo(() => layoutRead(nodes, edges), [nodes, edges])
  const summary = useMemo(() => readSummary(layout.counts), [layout.counts])
  const { setViewport } = useReactFlow()
  const box = useRef<HTMLDivElement>(null)

  // Open like a document: the top of the workflow, as wide as fits (between 55% and 85% so the words
  // stay readable), then scroll down. Re-framed when the workflow's shape or the free width changes, not per edit.
  const shape = `${layout.nodes.length}:${layout.edges.length}:${rightInset}`
  useEffect(() => {
    const el = box.current
    if (!el || layout.nodes.length === 0) return
    const xs = layout.nodes.map((n) => n.position.x)
    const minX = Math.min(...xs)
    const minY = Math.min(...layout.nodes.map((n) => n.position.y))
    const width = Math.max(...xs) + READ_NODE.width - minX
    const zoom = Math.min(0.85, Math.max(0.55, (el.clientWidth - 48) / width))
    void setViewport({ x: (el.clientWidth - width * zoom) / 2 - minX * zoom, y: 28 - minY * zoom, zoom })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, setViewport])

  if (layout.nodes.length === 0) {
    return <p className="pt-40 text-center text-sm text-muted-foreground">Nothing to read yet: this workflow has no steps.</p>
  }
  return (
    <div ref={box} className="absolute inset-0 top-[60px]" style={{ right: rightInset }}>
      <svg className="absolute h-0 w-0" aria-hidden>
        <defs>
          <marker id="rv-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="rv-arrow" />
          </marker>
        </defs>
      </svg>
      <ReactFlow
        nodes={layout.nodes}
        edges={layout.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={(_, n) => openNDV(n.id)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        edgesFocusable={false}
        panOnDrag
        panOnScroll
        zoomOnScroll={false}
        zoomOnPinch
        minZoom={0.15}
        proOptions={PRO_OPTIONS}
        className="bg-transparent"
      >
        <Background id="rv-grid" variant={BackgroundVariant.Dots} gap={20} size={1} className="[&>pattern>circle]:fill-(--grid-dot)" />
        <Toolbar summary={summary} />
      </ReactFlow>
    </div>
  )
}

export default function WorkflowReadView({ rightInset = 0 }: { rightInset?: number }) {
  return (
    <div className="rv absolute inset-0" data-testid="workflow-read-view">
      <ReactFlowProvider>
        <ReadFlow rightInset={rightInset} />
      </ReactFlowProvider>
    </div>
  )
}
