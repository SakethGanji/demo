/**
 * The glass node card (mockup `.en`): icon tile, mono kind line with the run mark, the step's name and its
 * one-line sentence. Ports are real React Flow handles (same ids as the legacy node, so edges, ⌘K and the
 * engine keep working); an unconnected output shows a + that opens the catalog wired to that port.
 * While an AI stage plays on the canvas, the legacy node renders instead, so the build animation stays intact.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Handle, Position, useUpdateNodeInternals, type Node, type NodeProps } from '@xyflow/react'
import { AlertTriangle, FileCheck2, FlaskConical, Pin, Plus } from 'lucide-react'
import { Tip } from '@/shared/components/ui/tooltip'
import { StatusMark } from '@/shared/glass/StatusMark'
import { cx } from '@/shared/glass/cx'
import WorkflowNode from '../../components/canvas/nodes/WorkflowNode'
import { NodeChips } from '../../components/interface/NodeChips'
import { SubworkflowBadge } from '../../components/canvas/nodes/SubworkflowBadge'
import { useStageActive } from '../../components/canvas/useStageActive'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useEditorLayoutStore } from '../../stores/editorLayoutStore'
import { useHistoryStore } from '../../stores/historyStore'
import { useCheckStore } from '../../stores/checkStore'
import { useTestStore } from '../../stores/testStore'
import { useConvertSubworkflowStore } from '../../stores/convertSubworkflowStore'
import { nodeTestBadge } from '../../lib/tests'
import { getIconForNode } from '../../lib/nodeIcons'
import { isTriggerType } from '../../lib/nodeConfig'
import { calculateHandlePositions } from '../../lib/nodeStyles'
import { useConnectedOutputHandles, useHasInputConnection, useNodeExecution } from '../../hooks/useWorkflowSelectors'
import type { WorkflowNodeData } from '../../types/workflow'
import { kindFor, sentenceFor, sentenceParts } from '../lib/pageSentences'
import { execToStatus } from './status'

type P = NodeProps<Node<WorkflowNodeData>>

function GlassNodeBody({ id, data, selected }: P) {
  const exec = useNodeExecution(id)
  const anyRunning = useWorkflowStore((s) => s.isAnyNodeRunning)
  const dragging = useWorkflowStore((s) => s.draggedNodeType !== null)
  const nodeTypesMap = useWorkflowStore((s) => s.nodeTypesMap)
  const pinned = useWorkflowStore((s) => (s.pinnedData[id]?.length ?? 0) > 0)
  const waiting = useWorkflowStore((s) => s.waitingRun?.tasks.some((t) => t.node_name === data.name) ?? false)
  const openForConnection = useEditorLayoutStore((s) => s.openForConnection)
  const diff = useHistoryStore((s) => s.marks[id])
  const refused = useConvertSubworkflowStore((s) => s.highlight.has(data.name))
  const findings = useCheckStore((s) => s.findingsByNodeMap[data.name])
  const marks = useMemo(() => (findings ?? []).filter((f) => f.severity !== 'info'), [findings])
  const check = marks.some((f) => f.severity === 'error') ? 'error' : marks.length ? 'warning' : null
  const testBadge = useTestStore((s) => nodeTestBadge(data.name, s.report, null))
  const hasInput = useHasInputConnection(id)
  const connected = useConnectedOutputHandles(id)

  const isTrigger = isTriggerType(data.type || '', nodeTypesMap)
  const inputCount = isTrigger ? 0 : Math.max(1, data.inputCount ?? data.inputs?.length ?? 1)
  const mainOutputs = Math.max(1, data.outputCount ?? data.outputs?.length ?? 1)
  const hasError = data.onError === 'error_output'
  const update = useUpdateNodeInternals()
  // Re-measure the handles only when they change. React Flow measures a node when it mounts; asking again from
  // every node's mount effect made one store update per node in a single effect flush, which on a long
  // workflow (36 steps) tripped React's "Maximum update depth exceeded".
  const handleKey = `${mainOutputs}:${hasError}:${inputCount}`
  const measured = useRef(handleKey)
  useEffect(() => {
    if (measured.current === handleKey) return
    measured.current = handleKey
    update(id)
  }, [id, handleKey, update])
  const inPos = useMemo(() => calculateHandlePositions(inputCount), [inputCount])
  const outPos = useMemo(() => calculateHandlePositions(mainOutputs), [mainOutputs])

  const Icon = useMemo(() => getIconForNode(data.icon, data.type), [data.icon, data.type])
  const sn = { name: data.name, type: data.type, label: data.label, parameters: data.parameters, notes: data.notes }
  const kind = kindFor(sn)
  const parts = sentenceParts(sentenceFor(sn))

  const status = execToStatus(exec?.status)
  const running = exec?.status === 'running'
  const idle = anyRunning && (!exec || exec.status === 'idle')
  const canDrop = dragging && !isTrigger && !hasInput
  const [hover, setHover] = useState(false)

  const outputs = Array.from({ length: mainOutputs }, (_, i) => {
    const def = data.outputs?.[i]
    const hid = def?.name || `output-${i}`
    const label = mainOutputs > 1 ? def?.displayName || def?.name || `${i + 1}` : ''
    return { hid, label, top: outPos[i] }
  })

  return (
    <div
      className={cx(
        'ge-node',
        isTrigger && 'trigger',
        selected && 'sel',
        running && 'live',
        !running && status === 'ok' && 'on',
        status === 'fail' && 'fail',
        (status === 'need' || waiting) && 'wait',
        idle && 'idle',
        canDrop && 'drop',
        data.disabled && 'disabled',
      )}
      data-testid="glass-node"
      data-name={data.name}
      data-status={status}
      data-diff={diff}
      data-convert-refused={refused || undefined}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {diff && diff !== 'removed' && <span className={cx('vb', diff === 'added' ? 'new' : 'chg')}>{diff === 'added' ? 'new' : 'changed'}</span>}
      {diff === 'removed' && <span className="vb gone">removed</span>}

      {inPos.map((top, i) => (
        <Handle
          key={`in-${i}`}
          type="target"
          position={Position.Left}
          id={data.inputs?.[i]?.name || `input-${i}`}
          className="ge-port"
          style={{ top: `${top}%` }}
        />
      ))}

      <span className="ic" aria-hidden>
        <Icon />
      </span>
      <div className="k">
        <span className="kt">{[kind.kind, ...kind.parts].join(' · ')}</span>
        {pinned && <Pin className="pini" aria-label="Pinned data" data-testid="glass-node-pinned" />}
        {waiting && <span className="wt">waiting</span>}
        <StatusMark status={status} size="sm" />
      </div>
      <div className="t">
        <span title={data.label}>{data.label}</span>
      </div>
      <div className="s">
        {parts.length
          ? parts.map((p, i) => (typeof p === 'string' ? <span key={i}>{p}</span> : <b key={i}>{p.token.value || p.token.label}</b>))
          : data.description || ' '}
      </div>
      {(data.type === 'Approval' || data.type === 'FormTrigger' || data.type === 'ExecuteWorkflow') && (
        <div className="x">
          {data.type === 'ExecuteWorkflow' ? (
            <SubworkflowBadge parameters={data.parameters as Record<string, unknown> | undefined} />
          ) : (
            <NodeChips type={data.type} name={data.name} parameters={data.parameters} />
          )}
        </div>
      )}

      {check && (
        <Tip label={<span className="flex flex-col gap-0.5">{marks.map((f, i) => <span key={i}>{f.message}</span>)}</span>}>
          <span className="bdg chk" data-testid="node-check-mark" data-check={check}>
            <AlertTriangle strokeWidth={3} />
          </span>
        </Tip>
      )}
      {data.contract && (
        <Tip label={`Contract approved by ${data.contract.approved_by} on ${new Date(data.contract.approved_at).toLocaleDateString()}`}>
          <span className="bdg ctr" data-testid="node-contract-badge">
            <FileCheck2 strokeWidth={2.5} />
          </span>
        </Tip>
      )}
      {testBadge && (
        <Tip label={testBadge === 'fail' ? 'A test fails at this step' : 'Tests covering this step pass'}>
          <span className="bdg tst" data-testid="node-test-badge" data-status={testBadge}>
            <FlaskConical strokeWidth={2.5} />
          </span>
        </Tip>
      )}

      {outputs.map((o) => (
        <span key={o.hid}>
          <Handle type="source" position={Position.Right} id={o.hid} className="ge-port" style={{ top: `${o.top}%` }} />
          {o.label && <span className="plabel" style={{ top: `${o.top}%` }}>{o.label}</span>}
          {!connected.has(o.hid) && (hover || selected) && (
            <button
              type="button"
              className="oplus nodrag"
              style={{ top: `${o.top}%` }}
              aria-label={`Add a step after ${data.label}${o.label ? ` (${o.label})` : ''}`}
              data-testid="glass-node-add"
              onClick={(e) => {
                e.stopPropagation()
                openForConnection(id, o.hid)
              }}
            >
              <Plus strokeWidth={2.5} />
            </button>
          )}
        </span>
      ))}
      {hasError && (
        <>
          <Handle type="source" position={Position.Bottom} id="error" className="ge-port err" data-testid="node-error-handle" />
          <span className="elabel">error</span>
          {!connected.has('error') && (hover || selected) && (
            <button
              type="button"
              className="oplus err nodrag"
              aria-label={`Add a step on ${data.label}'s error output`}
              onClick={(e) => {
                e.stopPropagation()
                openForConnection(id, 'error')
              }}
            >
              <Plus strokeWidth={2.5} />
            </button>
          )}
        </>
      )}
    </div>
  )
}

function GlassNode(props: P) {
  const stage = useStageActive()
  return stage ? <WorkflowNode {...props} /> : <GlassNodeBody {...props} />
}

export default memo(GlassNode)
