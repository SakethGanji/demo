/**
 * The node details modal (n8n-style), in glass: a centred sheet over the dimmed canvas with three resizable
 * panes, Input | Setup·Settings | Output. Opened by double-click / Enter on a node, the context menu, an edge's
 * "Open in modal", or the inspector's expand; driven by ndvStore (isOpen / activeNodeId / pane sizes).
 *
 * Data passing: the Input pane is the shared InputPanel (upstream picker, schema/JSON, drag a field into a
 * parameter → `{{ $json.field }}`); parameters preview their resolved value. "Test step" runs this one step
 * against the chosen input: This run (the upstream's items on the canvas now, sent as the input), Last run
 * (the engine's stored run), Pinned, or Typed JSON. The Output pane fills from the result and can be pinned.
 */
import { useCallback, useMemo, useRef, useState } from 'react'
import type { Node } from '@xyflow/react'
import { Dialog } from '@base-ui/react/dialog'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { ArrowLeft, ChevronLeft, ChevronRight, FlaskConical, Loader2, Trash2 } from 'lucide-react'
import CodeEditor from '@/shared/components/ui/code-editor'
import { StatusMark } from '@/shared/glass/StatusMark'
import { STATUS_LABEL, cx } from '@/shared/glass/cx'
import InputPanel from '../../components/ndv/InputPanel'
import OutputPanel from '../../components/ndv/OutputPanel'
import { useNDVStore } from '../../stores/ndvStore'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useNodeById, useNodeExecution } from '../../hooks/useWorkflowSelectors'
import { useNodeActions } from '../../hooks/useNodeActions'
import { describeActionError, testStep, type TestSource } from '../../lib/nodeActions'
import { getIconForNode } from '../../lib/nodeIcons'
import { isTriggerType } from '../../lib/nodeConfig'
import type { WorkflowNodeData } from '../../types/workflow'
import { kindFor } from '../lib/pageSentences'
import { InspectorSetup } from './InspectorSetup'
import { InspectorSettings } from './InspectorSettings'
import { unwrapItems } from './GlassEdge'
import { execToStatus, plural } from './status'

type Src = 'run' | 'last_run' | 'pinned' | 'input'
const SOURCES: { k: Src; label: string; hint: string }[] = [
  { k: 'run', label: 'This run', hint: 'What reached this step on the canvas now' },
  { k: 'last_run', label: 'Last run', hint: "The engine's last stored run" },
  { k: 'pinned', label: 'Pinned', hint: 'Pinned data upstream' },
  { k: 'input', label: 'Typed', hint: 'JSON you type' },
]

/** The items that reached `nodeId` in the run on the canvas: its recorded input, else its upstreams' outputs. */
export function itemsFromThisRun(nodeId: string): Record<string, unknown>[] | null {
  const { executionData, edges } = useWorkflowStore.getState()
  const own = executionData[nodeId]?.input?.items
  if (own && own.length) return unwrapItems(own)
  const ups = edges.filter((e) => e.target === nodeId)
  const out: Record<string, unknown>[] = []
  let any = false
  for (const e of ups) {
    const d = executionData[e.source]
    if (d?.output?.items) {
      any = true
      out.push(...unwrapItems(d.output.items))
    }
  }
  return any ? out : null
}

function SheetHeader({ node, onClose, onTest, testing }: { node: Node<WorkflowNodeData>; onClose: () => void; onTest: () => void; testing: boolean }) {
  const nodes = useWorkflowStore((s) => s.nodes)
  const nodeTypesMap = useWorkflowStore((s) => s.nodeTypesMap)
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData)
  const deleteNode = useWorkflowStore((s) => s.deleteNode)
  const openNDV = useNDVStore((s) => s.openNDV)
  const closeNDV = useNDVStore((s) => s.closeNDV)
  const exec = useNodeExecution(node.id)
  const status = execToStatus(exec?.status)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(node.data.label)

  const steps = useMemo(
    () => nodes.filter((n) => n.type === 'workflowNode').slice().sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y),
    [nodes],
  )
  const idx = steps.findIndex((n) => n.id === node.id)
  const Icon = getIconForNode(node.data.icon, node.data.type)
  const trigger = isTriggerType(node.data.type || '', nodeTypesMap)
  const kind = kindFor({ name: node.data.name, type: node.data.type, label: node.data.label, parameters: node.data.parameters })

  const save = () => {
    const v = name.trim()
    if (v && v !== node.data.label) {
      useWorkflowStore.getState().saveToHistory()
      updateNodeData(node.id, { label: v })
    }
    else setName(node.data.label)
    setEditing(false)
  }

  return (
    <div className="ge-nh">
      <button type="button" className="ge-btn ghost sm" onClick={onClose} data-testid="ndv-back">
        <ArrowLeft /> Back to canvas
      </button>
      <span className={cx('ge-nic', trigger && 'trigger')} aria-hidden>
        <Icon />
      </span>
      <div className="ge-nt">
        <span className="k">{[kind.kind, ...kind.parts].join(' · ')}</span>
        <h4>
          {editing ? (
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={save}
              onKeyDown={(e) => {
                if (e.key === 'Enter') save()
                if (e.key === 'Escape') {
                  e.stopPropagation()
                  setName(node.data.label)
                  setEditing(false)
                }
              }}
              aria-label="Step name"
              data-testid="ndv-name-input"
            />
          ) : (
            <Dialog.Title
              render={
                <button
                  type="button"
                  className="nm"
                  title="Rename"
                  onClick={() => {
                    setName(node.data.label)
                    setEditing(true)
                  }}
                  data-testid="ndv-rename"
                />
              }
            >
              {node.data.label}
            </Dialog.Title>
          )}
          {exec && exec.status !== 'idle' && (
            <span className={cx('ge-chip', status === 'ok' && 'ok', status === 'fail' && 'err', status === 'need' && 'warn')} data-testid="ndv-status">
              <StatusMark status={status} size="sm" /> {STATUS_LABEL[status]}
            </span>
          )}
        </h4>
      </div>
      <span className="sp" />
      <div className="ge-npick" data-testid="ndv-node-picker">
        <button type="button" className="ge-ib" aria-label="Previous step" disabled={idx <= 0} onClick={() => idx > 0 && openNDV(steps[idx - 1].id)} data-testid="ndv-prev">
          <ChevronLeft />
        </button>
        <select value={node.id} onChange={(e) => openNDV(e.target.value)} aria-label="Go to step" data-testid="ndv-node-select">
          {steps.map((n) => (
            <option key={n.id} value={n.id}>{(n.data as WorkflowNodeData).label}</option>
          ))}
        </select>
        <button
          type="button"
          className="ge-ib"
          aria-label="Next step"
          disabled={idx < 0 || idx >= steps.length - 1}
          onClick={() => idx >= 0 && idx < steps.length - 1 && openNDV(steps[idx + 1].id)}
          data-testid="ndv-next"
        >
          <ChevronRight />
        </button>
      </div>
      <button type="button" className="ge-btn primary sm" onClick={onTest} disabled={testing} data-testid="ndv-test-step">
        {testing ? <Loader2 className="animate-spin" /> : <FlaskConical />} {testing ? 'Running…' : 'Test step'}
      </button>
      <button
        type="button"
        className="ge-ib"
        aria-label="Delete step"
        title="Delete step"
        onClick={() => {
          deleteNode(node.id)
          closeNDV()
        }}
        data-testid="ndv-delete"
      >
        <Trash2 />
      </button>
    </div>
  )
}

function SheetBody({ node }: { node: Node<WorkflowNodeData> }) {
  const closeNDV = useNDVStore((s) => s.closeNDV)
  const inSize = useNDVStore((s) => s.inputPanelSize)
  const outSize = useNDVStore((s) => s.outputPanelSize)
  const exec = useNodeExecution(node.id)
  const actions = useNodeActions()
  const [src, setSrc] = useState<Src>('run')
  const [typed, setTyped] = useState('{\n  \n}')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [tab, setTab] = useState<'setup' | 'settings'>('setup')
  const busy = useRef(false)

  const runTest = useCallback(async () => {
    if (busy.current) return
    setMsg(null)
    let source: TestSource = src === 'run' ? 'input' : src
    let items: Record<string, unknown>[] | undefined
    if (src === 'input') {
      try {
        const parsed: unknown = JSON.parse(typed)
        const list = Array.isArray(parsed) ? parsed : [parsed]
        if (!list.every((i) => i && typeof i === 'object' && !Array.isArray(i))) throw new Error('Each item must be a JSON object')
        items = list as Record<string, unknown>[]
      } catch (e) {
        setMsg({ ok: false, text: `Typed input is not valid JSON: ${(e as Error).message}` })
        return
      }
    } else if (src === 'run') {
      const fromRun = itemsFromThisRun(node.id)
      if (fromRun) items = fromRun
      else source = 'last_run' // nothing on the canvas yet: fall back to the engine's last run
    }
    busy.current = true
    setTesting(true)
    try {
      const res = await testStep(node.id, source, items, actions.saveNew)
      if (!res.ok) setMsg({ ok: false, text: res.message })
      else if (res.failed) setMsg({ ok: false, text: res.firstError ?? 'The step failed' })
      else {
        const n = useWorkflowStore.getState().executionData[node.id]?.output?.items?.length ?? 0
        setMsg({ ok: true, text: `Step ran · ${plural(n, 'item')} out` })
      }
    } catch (e) {
      setMsg({ ok: false, text: describeActionError(e).message })
    } finally {
      busy.current = false
      setTesting(false)
    }
  }, [src, typed, node.id, actions])

  return (
    <>
      <SheetHeader node={node} onClose={closeNDV} onTest={runTest} testing={testing} />
      <div className="ge-typed-wrap">
        <div className="ge-srcrow">
          <div className="ge-srcsw" data-testid="ndv-test-source">
            Test with
            <div className="ge-seg sm" role="group" aria-label="Test step input">
              {SOURCES.map((s) => (
                <button
                  key={s.k}
                  type="button"
                  title={s.hint}
                  aria-pressed={src === s.k}
                  onClick={() => {
                    setSrc(s.k)
                    setMsg(null)
                  }}
                  data-testid={`ndv-test-source-${s.k === 'last_run' ? 'last-run' : s.k}`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          {msg && (
            <div className={cx('ge-msg', msg.ok && 'ok')} role={msg.ok ? 'status' : 'alert'} data-testid={msg.ok ? 'ndv-test-ok' : 'ndv-test-error'}>
              {msg.text}
            </div>
          )}
        </div>
        {src === 'input' && (
          <div className="ge-typed">
            <CodeEditor value={typed} onChange={setTyped} language="json" minHeight="56px" maxHeight="140px" testId="ndv-test-input-editor" placeholder='{ "field": "value" }' />
          </div>
        )}
      </div>
      <div className="ge-panes">
        <PanelGroup direction="horizontal" autoSaveId={undefined}>
          <Panel
            defaultSize={inSize}
            minSize={15}
            maxSize={40}
            onResize={(size) => useNDVStore.getState().setPanelSizes(size, useNDVStore.getState().outputPanelSize)}
          >
            <section className="ge-pane" aria-label="Input" data-testid="ndv-pane-input">
              <div className="ge-pb ge-leaf">
                <InputPanel nodeId={node.id} executionData={exec} />
              </div>
            </section>
          </Panel>
          <PanelResizeHandle className="ge-gut" />
          <Panel defaultSize={100 - inSize - outSize} minSize={30}>
            <section className="ge-pane mid" aria-label="Parameters" data-testid="ndv-pane-setup">
              <div className="ge-ph">
                <div className="ge-itabs" role="tablist" aria-label="Step">
                  <button type="button" role="tab" aria-selected={tab === 'setup'} onClick={() => setTab('setup')} data-testid="ndv-tab-parameters">Setup</button>
                  <button type="button" role="tab" aria-selected={tab === 'settings'} onClick={() => setTab('settings')} data-testid="ndv-tab-settings">Settings</button>
                </div>
              </div>
              <div className="ge-pb pad">{tab === 'setup' ? <InspectorSetup node={node} /> : <InspectorSettings node={node} />}</div>
            </section>
          </Panel>
          <PanelResizeHandle className="ge-gut" />
          <Panel
            defaultSize={outSize}
            minSize={15}
            maxSize={40}
            onResize={(size) => useNDVStore.getState().setPanelSizes(useNDVStore.getState().inputPanelSize, size)}
          >
            <section className="ge-pane" aria-label="Output" data-testid="ndv-pane-output">
              <div className="ge-pb ge-leaf">
                <OutputPanel nodeId={node.id} executionData={exec} />
              </div>
              <div className="ge-pf">
                <button type="button" className="ge-btn sm" onClick={runTest} disabled={testing} data-testid="ndv-output-test-step">
                  <FlaskConical /> Test step
                </button>
              </div>
            </section>
          </Panel>
        </PanelGroup>
      </div>
    </>
  )
}

export function NodeSheet() {
  const isOpen = useNDVStore((s) => s.isOpen)
  const activeId = useNDVStore((s) => s.activeNodeId)
  const closeNDV = useNDVStore((s) => s.closeNDV)
  const node = useNodeById(activeId) as Node<WorkflowNodeData> | null
  const open = isOpen && !!node
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && closeNDV()}>
      <Dialog.Portal>
        <Dialog.Backdrop className="ge ge-ndvdim" data-testid="ndv-scrim" />
        <Dialog.Popup className="ge ge-ndv" data-testid="ndv-modal" aria-describedby={undefined}>
          {node && <SheetBody key={node.id} node={node} />}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export default NodeSheet
