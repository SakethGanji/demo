/**
 * The glass edge: the legacy edge (path, waypoints, hover +, run sparks) plus, after a run, a pill with the
 * number of items that passed along it. Clicking the pill opens a small glass popover with those items as a
 * table or JSON and an "Open in modal" that opens the target step's node sheet (its Input pane shows them).
 */
import { memo, useMemo, useRef, useState } from 'react'
import { EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from '@xyflow/react'
import { DataTablePreview } from '@/shared/glass/DataTablePreview'
import WorkflowEdge from '../../components/canvas/edges/WorkflowEdge'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useNDVStore } from '../../stores/ndvStore'
import { Pop } from './ui'
import { plural } from './status'

type Item = Record<string, unknown>

/** Engine items are plain objects; some paths wrap them as `{ json }`. */
export function unwrapItems(items: unknown[] | null | undefined): Item[] {
  return (items ?? []).map((it) => {
    if (it && typeof it === 'object' && !Array.isArray(it)) {
      const o = it as Item
      if ('json' in o && o.json && typeof o.json === 'object' && Object.keys(o).length <= 2) return o.json as Item
      return o
    }
    return { value: it }
  })
}

/**
 * The items that travelled along one edge: the target's input when this is its only incoming edge (exact),
 * otherwise the source's output on that handle's branch (best available). Null until the source has run.
 */
export function useEdgeItems(edgeId: string, source: string, target: string): Item[] | null {
  const src = useWorkflowStore((s) => s.executionData[source])
  const tgt = useWorkflowStore((s) => s.executionData[target])
  const incoming = useWorkflowStore((s) => s.edges.filter((e) => e.target === target).length)
  return useMemo(() => {
    void edgeId
    if (!src || src.status === 'idle' || src.status === 'running') return null
    if (incoming === 1 && tgt?.input?.items) return unwrapItems(tgt.input.items)
    if (src.output?.items) return unwrapItems(src.output.items)
    return null
  }, [edgeId, src, tgt, incoming])
}

function EdgeCount({ id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition }: EdgeProps) {
  const items = useEdgeItems(id, source, target)
  const openNDV = useNDVStore((s) => s.openNDV)
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'table' | 'json'>('table')
  const anchor = useRef<HTMLButtonElement>(null)
  const [, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  if (!items) return null
  const n = items.length
  return (
    <EdgeLabelRenderer>
      <div
        className="ge ge-ecount-wrap nodrag nopan"
        style={{ transform: `translate(-50%, -100%) translate(${lx}px, ${ly - 12}px)` }}
      >
        <button
          ref={anchor}
          type="button"
          className="ge-ecount"
          data-testid="edge-count"
          data-edge={id}
          data-source={source}
          data-target={target}
          data-count={n}
          aria-label={`${plural(n, 'item')} passed from this step to the next`}
          aria-expanded={open}
          onClick={(e) => {
            e.stopPropagation()
            setOpen((o) => !o)
          }}
        >
          {n}
        </button>
        <Pop open={open} onClose={() => setOpen(false)} anchor={anchor} className="ge-epop" testId="edge-popover" label="Items on this connection">
          <div className="ge-epop-h">
            <b>{plural(n, 'item')}</b>
            <div className="ge-seg sm" role="group" aria-label="View as">
              <button type="button" aria-pressed={mode === 'table'} onClick={() => setMode('table')}>Table</button>
              <button type="button" aria-pressed={mode === 'json'} onClick={() => setMode('json')} data-testid="edge-popover-json">JSON</button>
            </div>
          </div>
          <div className="ge-epop-b">
            {n === 0 ? (
              <p className="ge-empty">No items passed.</p>
            ) : mode === 'table' ? (
              <DataTablePreview rows={items} preview={5} data-testid="edge-popover-table" />
            ) : (
              <pre className="ge-mono ge-epop-json" data-testid="edge-popover-pre">{JSON.stringify(items.slice(0, 20), null, 2)}</pre>
            )}
          </div>
          <div className="ge-epop-f">
            <button
              type="button"
              className="ge-lnk"
              data-testid="edge-popover-open"
              onClick={() => {
                setOpen(false)
                openNDV(target)
              }}
            >
              Open in modal ▸
            </button>
          </div>
        </Pop>
      </div>
    </EdgeLabelRenderer>
  )
}

function GlassEdge(props: EdgeProps) {
  return (
    <>
      <WorkflowEdge {...props} />
      <EdgeCount {...props} />
    </>
  )
}

export default memo(GlassEdge)
