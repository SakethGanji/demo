/**
 * The step catalog (mockup `.palette`): search the engine's node types and add one. Opened by the tools bar
 * "+", a node's "+" handle (connects the new step to it), ⌘K "Add node", or dropping an edge on empty canvas.
 * It answers the same layout-store request the legacy creator panel did (rightPanelOpen + tab 'nodes').
 */
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Loader2, Search, X } from 'lucide-react'
import { useEditorLayoutStore } from '../../stores/editorLayoutStore'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useNodeTypes } from '../../hooks/useNodeTypes'
import { generateNodeName, getExistingNodeNames } from '../../lib/workflowTransform'
import { getNodeIcon } from '../../lib/nodeConfig'
import { getIconForNode } from '../../lib/nodeIcons'
import { createWorkflowNodeData } from '../../lib/createNodeData'
import type { NodeGroup, NodeIO } from '../../lib/nodeStyles'
import type { OutputStrategy } from '../../types/workflow'
import type { ApiProperty } from '@/shared/lib/api'
import { cx } from '@/shared/glass/cx'
import { useGlassStore } from '../state/glassStore'
import { isTriggerData } from './status'

interface Entry {
  type: string
  displayName: string
  description: string
  icon: string
  group: NodeGroup
  trigger: boolean
  inputCount: number
  outputCount: number
  inputs: NodeIO[]
  outputs: NodeIO[]
  outputStrategy?: OutputStrategy
  properties?: ApiProperty[]
}

const GROUP_LABEL: Record<string, string> = { trigger: 'Starts the workflow', transform: 'Shape data', flow: 'Flow', ai: 'AI', helper: 'Helpers', other: 'Other' }

function useEntries(): { entries: Entry[]; isLoading: boolean; isError: boolean } {
  const { data, isLoading, isError } = useNodeTypes()
  const entries = useMemo(
    () =>
      (data ?? []).map((n): Entry => {
        const inputs = (n.inputs || []).map((i: { name: string; displayName?: string }) => ({ name: i.name, displayName: i.displayName || i.name }))
        const outputs = (n.outputs || []).map((o: { name: string; displayName?: string }) => ({ name: o.name, displayName: o.displayName || o.name }))
        return {
          type: n.type,
          displayName: n.displayName,
          description: n.description,
          icon: getNodeIcon(n.type, n.icon),
          group: (n.group?.[0] || 'other') as NodeGroup,
          trigger: !!n.group?.includes('trigger'),
          inputCount: typeof n.inputCount === 'number' ? n.inputCount : inputs.length,
          outputCount: typeof n.outputCount === 'number' ? n.outputCount : n.outputCount === 'dynamic' ? outputs.length || 1 : outputs.length,
          inputs,
          outputs,
          outputStrategy: n.outputStrategy as OutputStrategy | undefined,
          properties: n.properties as ApiProperty[] | undefined,
        }
      }),
    [data],
  )
  return { entries, isLoading, isError }
}

export function NodeCatalog() {
  const open = useEditorLayoutStore((s) => s.rightPanelOpen && s.rightPanelTab === 'nodes')
  const view = useEditorLayoutStore((s) => s.nodeCreatorView)
  const sourceNodeId = useEditorLayoutStore((s) => s.sourceNodeId)
  const sourceHandleId = useEditorLayoutStore((s) => s.sourceHandleId)
  const dropPosition = useEditorLayoutStore((s) => s.dropPosition)
  const nodes = useWorkflowStore((s) => s.nodes)
  const sourceLabel = useWorkflowStore((s) => (sourceNodeId ? (s.nodes.find((n) => n.id === sourceNodeId)?.data as { label?: string } | undefined)?.label : undefined))
  const { entries, isLoading, isError } = useEntries()
  const [q, setQ] = useState('')
  const [cur, setCur] = useState(0)
  const ref = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  const hasTrigger = nodes.some((n) => n.type === 'workflowNode' && isTriggerData(n.data as never))
  const onlyTriggers = view === 'trigger' && !hasTrigger && !sourceNodeId

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    let list = onlyTriggers ? entries.filter((e) => e.trigger) : sourceNodeId ? entries.filter((e) => !e.trigger) : entries
    if (s) list = list.filter((e) => e.displayName.toLowerCase().includes(s) || e.description.toLowerCase().includes(s) || e.type.toLowerCase().includes(s))
    return list
  }, [entries, q, onlyTriggers, sourceNodeId])

  const groups = useMemo(() => {
    const g: [string, Entry[]][] = []
    for (const e of shown) {
      const key = e.trigger ? 'trigger' : e.group
      let row = g.find((x) => x[0] === key)
      if (!row) g.push((row = [key, []]))
      row[1].push(e)
    }
    return g
  }, [shown])
  const flat = useMemo(() => groups.flatMap((g) => g[1]), [groups])

  useEffect(() => {
    if (!open) return
    setQ('')
    setCur(0)
    requestAnimationFrame(() => input.current?.focus())
  }, [open, sourceNodeId])

  const close = () => {
    const lay = useEditorLayoutStore.getState()
    lay.closeCreatorPanel()
    lay.closeRightPanel()
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node) && !(e.target as HTMLElement).closest?.('[data-catalog-opener]')) close()
    }
    const t = setTimeout(() => document.addEventListener('pointerdown', onDown), 0)
    return () => {
      clearTimeout(t)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  const add = (e: Entry) => {
    const ws = useWorkflowStore.getState()
    const all = ws.nodes
    let position: { x: number; y: number }
    const src = sourceNodeId ? all.find((n) => n.id === sourceNodeId) : undefined
    if (dropPosition) position = { x: Math.round(dropPosition.x / 20) * 20, y: Math.round(dropPosition.y / 20) * 20 }
    else if (src) position = { x: src.position.x + 250, y: src.position.y }
    else if (!all.filter((n) => n.type === 'workflowNode').length) position = { x: 250, y: 200 }
    else position = { x: Math.max(...all.map((n) => n.position.x)) + 250, y: all.reduce((s, n) => s + n.position.y, 0) / all.length }
    const id = `node-${Date.now()}`
    const name = generateNodeName(e.type, getExistingNodeNames(all as never))
    const data = createWorkflowNodeData(
      {
        type: e.type,
        displayName: e.displayName,
        icon: e.icon,
        description: e.description,
        group: e.group,
        inputCount: e.inputCount,
        outputCount: e.outputCount,
        inputs: e.inputs,
        outputs: e.outputs,
        outputStrategy: e.outputStrategy,
        properties: e.properties,
      },
      { name },
    )
    ws.addNode({ id, type: 'workflowNode', position, data })
    if (sourceNodeId) ws.onConnect({ source: sourceNodeId, target: id, sourceHandle: sourceHandleId, targetHandle: null })
    close()
    // the new step opens in the inspector, ready to set up
    ws.setSelectedNode(id)
    useGlassStore.getState().select(name)
    useGlassStore.getState().setInspector('compact')
  }

  if (!open) return null
  let i = -1
  return (
    <div
      ref={ref}
      className="ge-fl ge-palette"
      role="dialog"
      aria-label="Add a step"
      data-testid="ge-catalog"
      onKeyDown={(ev) => {
        if (ev.key === 'Escape') (ev.stopPropagation(), close())
        else if (ev.key === 'ArrowDown') (ev.preventDefault(), setCur((c) => Math.min(flat.length - 1, c + 1)))
        else if (ev.key === 'ArrowUp') (ev.preventDefault(), setCur((c) => Math.max(0, c - 1)))
        else if (ev.key === 'Enter' && flat[cur]) (ev.preventDefault(), add(flat[cur]))
      }}
    >
      <div className="ctx">
        {sourceNodeId ? `Add a step after ${sourceLabel ?? 'this step'}` : onlyTriggers ? 'How does the workflow start?' : 'Add a step'}
        <span style={{ flex: 1 }} />
        <button type="button" className="ge-ib" aria-label="Close" onClick={close} data-testid="ge-catalog-close">
          <X />
        </button>
      </div>
      <label className="ge-srch">
        <Search />
        <input
          ref={input}
          value={q}
          onChange={(e) => (setQ(e.target.value), setCur(0))}
          placeholder="Search steps…"
          aria-label="Search steps"
          data-testid="ge-catalog-search"
        />
      </label>
      <div className="pall">
        {isLoading ? (
          <p className="ge-hint"><Loader2 className="animate-spin" /> Loading steps…</p>
        ) : isError ? (
          <p className="ge-hint">Couldn't load the step types.</p>
        ) : flat.length === 0 ? (
          <p className="ge-hint">No steps match "{q}".</p>
        ) : (
          groups.map(([g, list]) => (
            <div key={g} style={{ display: 'contents' }}>
              <h6>
                {GROUP_LABEL[g] ?? g.charAt(0).toUpperCase() + g.slice(1)}
                <span>{list.length}</span>
              </h6>
              {list.map((e) => {
                i += 1
                const idx = i
                const Icon = getIconForNode(e.icon, e.type)
                return (
                  <button
                    key={e.type}
                    type="button"
                    className={cx('ge-pi', e.trigger && 'trigger', idx === cur && 'cur')}
                    onClick={() => add(e)}
                    onMouseEnter={() => setCur(idx)}
                    draggable
                    onDragStart={(ev: DragEvent<HTMLButtonElement>) => {
                      ev.dataTransfer.setData('application/reactflow-node', JSON.stringify({ ...e, name: e.type, category: e.group }))
                      ev.dataTransfer.effectAllowed = 'move'
                      useWorkflowStore.getState().setDraggedNodeType(e.type)
                    }}
                    onDragEnd={() => useWorkflowStore.getState().setDraggedNodeType(null)}
                    data-testid="ge-catalog-item"
                    data-type={e.type}
                  >
                    <span className="ic"><Icon size={14} /></span>
                    <span className="nm">
                      <b>{e.displayName}</b>
                      <small>{e.description}</small>
                    </span>
                  </button>
                )
              })}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default NodeCatalog
