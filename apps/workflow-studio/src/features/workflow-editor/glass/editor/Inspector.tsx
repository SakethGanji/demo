/**
 * The inspector (mockup `.insp`): the selected step on the right. Header with its kind, status and name (click
 * to rename); tabs Setup / Data / Settings; footer Delete · Test step · Run to · Run from. Expanding (↗) opens
 * the node sheet. A trigger's Setup also holds the test payload.
 */
import { useEffect, useState } from 'react'
import type { Node } from '@xyflow/react'
import { FlaskConical, Loader2, Maximize2, Trash2, X } from 'lucide-react'
import { StatusMark } from '@/shared/glass/StatusMark'
import { STATUS_LABEL, cx } from '@/shared/glass/cx'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useNDVStore } from '../../stores/ndvStore'
import { useNodeExecution } from '../../hooks/useWorkflowSelectors'
import { useNodeActions } from '../../hooks/useNodeActions'
import { testStep } from '../../lib/nodeActions'
import type { WorkflowNodeData } from '../../types/workflow'
import { kindFor } from '../lib/pageSentences'
import { useGlassStore } from '../state/glassStore'
import { InspectorSetup } from './InspectorSetup'
import { InspectorData } from './InspectorData'
import { InspectorSettings } from './InspectorSettings'
import { TestPayload } from './TestPayload'
import { itemsFromThisRun } from './NodeSheet'
import { execToStatus, isTriggerData, plural } from './status'

type Tab = 'setup' | 'data' | 'settings'

function Body({ node }: { node: Node<WorkflowNodeData> }) {
  const mode = useGlassStore((s) => s.inspector)
  const setInspector = useGlassStore((s) => s.setInspector)
  const select = useGlassStore((s) => s.select)
  const payloadSource = useGlassStore((s) => s.payloadSource)
  const nodeTypesMap = useWorkflowStore((s) => s.nodeTypesMap)
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData)
  const deleteNode = useWorkflowStore((s) => s.deleteNode)
  const openNDV = useNDVStore((s) => s.openNDV)
  const exec = useNodeExecution(node.id)
  const actions = useNodeActions()
  const status = execToStatus(exec?.status)
  void nodeTypesMap
  const trigger = isTriggerData(node.data)
  const kind = kindFor({ name: node.data.name, type: node.data.type, label: node.data.label, parameters: node.data.parameters })
  const [tab, setTab] = useState<Tab>('setup')
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(node.data.label)
  const [busy, setBusy] = useState<null | 'test' | 'to' | 'from'>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    // "With a typed payload…" from the header lands on the trigger's Setup
    if (trigger && payloadSource.kind === 'typed') setTab('setup')
  }, [trigger, payloadSource])

  const saveName = () => {
    const v = name.trim()
    if (v && v !== node.data.label) {
      useWorkflowStore.getState().saveToHistory()
      updateNodeData(node.id, { label: v })
    }
    else setName(node.data.label)
    setEditing(false)
  }
  const close = () => {
    setInspector('closed')
    select(null)
    useWorkflowStore.getState().setSelectedNode(null)
  }
  const run = async (what: 'test' | 'to' | 'from') => {
    setBusy(what)
    setMsg(null)
    try {
      if (what === 'test') {
        const items = itemsFromThisRun(node.id)
        const res = await testStep(node.id, items ? 'input' : 'last_run', items ?? undefined, actions.saveNew)
        if (!res.ok) setMsg({ ok: false, text: res.message })
        else if (res.failed) setMsg({ ok: false, text: res.firstError ?? 'The step failed' })
        else setMsg({ ok: true, text: `Step ran · ${plural(useWorkflowStore.getState().executionData[node.id]?.output?.items?.length ?? 0, 'item')} out` })
        setTab('data')
      } else {
        const res = what === 'to' ? await actions.runTo(node.id) : await actions.runFrom(node.id)
        if (res.ok && !res.failed) setMsg({ ok: true, text: what === 'to' ? 'Ran up to this step' : 'Ran from this step' })
      }
    } finally {
      setBusy(null)
    }
  }

  return (
    <aside className={cx('ge-fl ge-insp', mode === 'expanded' && 'wide')} aria-label={`Step: ${node.data.label}`} data-testid="ge-inspector" data-node={node.data.name}>
      <div className="ge-ih">
        <div className="k">
          <StatusMark status={status} size="sm" />
          <span>{[kind.kind, ...kind.parts].join(' · ')}</span>
          <span className="sp" style={{ flex: 1 }} />
          <button type="button" className="ge-ib" aria-label="Open the step with its input and output" title="Open (↵)" onClick={() => openNDV(node.id)} data-testid="inspector-expand">
            <Maximize2 />
          </button>
          <button type="button" className="ge-ib" aria-label="Close" onClick={close} data-testid="inspector-close">
            <X />
          </button>
        </div>
        <h4>
          {editing ? (
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => {
                if (e.key === 'Enter') saveName()
                if (e.key === 'Escape') {
                  setName(node.data.label)
                  setEditing(false)
                }
              }}
              aria-label="Step name"
              data-testid="inspector-name-input"
            />
          ) : (
            <button type="button" className="nm" title="Rename" onClick={() => (setName(node.data.label), setEditing(true))} data-testid="inspector-name">
              {node.data.label}
            </button>
          )}
          {exec && exec.status !== 'idle' && (
            <span className={cx('ge-chip', status === 'ok' && 'ok', status === 'fail' && 'err', status === 'need' && 'warn')}>{STATUS_LABEL[status]}</span>
          )}
        </h4>
        <span className="did">{node.data.name}</span>
        <div className="ge-itabs" role="tablist" aria-label="Step">
          {(['setup', 'data', 'settings'] as Tab[]).map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} data-testid={`inspector-tab-${t}`}>
              {t === 'setup' ? 'Setup' : t === 'data' ? 'Data' : 'Settings'}
              {t === 'data' && exec && exec.status !== 'idle' && <span className="tdt" />}
            </button>
          ))}
        </div>
      </div>
      <div className="ge-ib2">
        {tab === 'setup' && (
          <>
            <InspectorSetup node={node} />
            {trigger && <TestPayload triggerName={node.data.name} />}
          </>
        )}
        {tab === 'data' && <InspectorData nodeId={node.id} />}
        {tab === 'settings' && <InspectorSettings node={node} />}
        {msg && (
          <div className={cx('ge-msg', msg.ok && 'ok')} role={msg.ok ? 'status' : 'alert'} data-testid={msg.ok ? 'inspector-ok' : 'inspector-error'}>
            {msg.text}
          </div>
        )}
      </div>
      <div className="ge-ifoot">
        <button
          type="button"
          className="ge-btn dsoft sm"
          onClick={() => {
            deleteNode(node.id)
            close()
          }}
          data-testid="inspector-delete"
        >
          <Trash2 /> Delete
        </button>
        <span className="sp" />
        <button type="button" className="ge-btn sm" onClick={() => void run('to')} disabled={!!busy} title="Run the workflow up to this step" data-testid="inspector-run-to">
          {busy === 'to' && <Loader2 className="animate-spin" />} Run to
        </button>
        <button type="button" className="ge-btn sm" onClick={() => void run('from')} disabled={!!busy} title="Run from this step, reusing earlier output" data-testid="inspector-run-from">
          {busy === 'from' && <Loader2 className="animate-spin" />} Run from
        </button>
        <button type="button" className="ge-btn primary sm" onClick={() => void run('test')} disabled={!!busy} data-testid="inspector-test-step">
          {busy === 'test' ? <Loader2 className="animate-spin" /> : <FlaskConical />} Test step
        </button>
      </div>
    </aside>
  )
}

export function Inspector() {
  const mode = useGlassStore((s) => s.inspector)
  const selected = useGlassStore((s) => s.selectedNode)
  const node = useWorkflowStore((s) => (selected ? s.nodes.find((n) => n.type === 'workflowNode' && (n.data as WorkflowNodeData).name === selected) : undefined)) as
    | Node<WorkflowNodeData>
    | undefined
  if (mode === 'closed' || !node) return null
  return <Body key={node.id} node={node} />
}

export default Inspector
