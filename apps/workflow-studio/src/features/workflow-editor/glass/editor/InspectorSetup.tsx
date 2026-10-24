/**
 * Setup: the step's one-line sentence, then its parameters (the engine's node-type schema through the shared
 * DynamicNodeForm leaf, with extensions such as cURL import and webhook URL). Expressions show their resolved
 * value against the upstream's data (ExpressionPreviewContext), and fields dragged from the Input pane drop in.
 */
import { useCallback, useMemo } from 'react'
import type { Node } from '@xyflow/react'
import DynamicNodeForm, { type NodeProperty, type OutputSchema } from '../../components/ndv/DynamicNodeForm'
import { ExpressionPreviewContext, ExpressionSampleContext } from '../../components/ndv/ExpressionEditor'
import { getNodeExtensions } from '../../components/ndv/extensions/NodeExtensions'
import { useNodeTypes } from '../../hooks/useNodeTypes'
import { useAllNodeExecData, useNodeById, useUpstreamNodeId, useUpstreamSampleData } from '../../hooks/useWorkflowSelectors'
import { isExtensionOwnedParam } from '../../lib/jsonParam'
import { useWorkflowStore } from '../../stores/workflowStore'
import type { WorkflowNodeData } from '../../types/workflow'
import { sentenceFor, sentenceParts } from '../lib/pageSentences'

export function NodeSentence({ node }: { node: Node<WorkflowNodeData> }) {
  const d = node.data
  const parts = sentenceParts(sentenceFor({ name: d.name, type: d.type, label: d.label, parameters: d.parameters, notes: d.notes }))
  if (!parts.length) return null
  return (
    <div className="ge-sent" data-testid="inspector-sentence">
      <p>
        {parts.map((p, i) => (typeof p === 'string' ? <span key={i}>{p}</span> : <b key={i}>{p.token.value || p.token.label}</b>))}
      </p>
    </div>
  )
}

export function InspectorSetup({ node, sentence = true }: { node: Node<WorkflowNodeData>; sentence?: boolean }) {
  const params = useMemo(() => (node.data.parameters as Record<string, unknown>) || {}, [node.data.parameters])
  const onChange = useCallback(
    (key: string, value: unknown) => {
      const store = useWorkflowStore.getState()
      const cur = store.nodes.find((n) => n.id === node.id)
      if (!cur) return
      store.updateNodeData(node.id, { parameters: { ...(cur.data as WorkflowNodeData).parameters, [key]: value } })
    },
    [node.id],
  )
  const { data: nodeTypes, isLoading } = useNodeTypes()
  const schema = nodeTypes?.find((n) => n.type === node.data.type)
  const props = useMemo(
    () => ((schema?.properties ?? []) as NodeProperty[]).filter((p) => !isExtensionOwnedParam(node.data.type, p.name, params)),
    [schema, node.data.type, params],
  )
  const upId = useUpstreamNodeId(node.id)
  const up = useNodeById(upId)
  const upSchema = up?.data?.type ? (nodeTypes?.find((n) => n.type === up.data.type)?.outputs?.[0]?.schema as OutputSchema | undefined) : undefined
  const sample = useUpstreamSampleData(upId)
  const all = useAllNodeExecData()
  const sampleCtx = useMemo(() => ({ sampleData: sample, allNodeData: all }), [sample, all])

  return (
    <ExpressionPreviewContext.Provider value>
      <div className="ge-setup" data-testid="inspector-setup">
        {sentence && <NodeSentence node={node} />}
        {getNodeExtensions(node.data.type).map((Ext, i) => (
          <div key={i} className="ge-leaf">
            <Ext node={node} />
          </div>
        ))}
        <section className="ge-isec">
          <h6>Parameters</h6>
          {isLoading ? (
            <p className="ge-hint">Loading the step's fields…</p>
          ) : schema && props.length > 0 ? (
            <div className="ge-leaf">
              <ExpressionSampleContext.Provider value={sampleCtx}>
              <DynamicNodeForm
                properties={props}
                values={params}
                onChange={onChange}
                allValues={params}
                upstreamSchema={upSchema}
                sampleData={sample}
                allNodeData={all}
              />
              </ExpressionSampleContext.Provider>
            </div>
          ) : schema ? (
            <p className="ge-empty">This step has nothing to set up.</p>
          ) : (
            <p className="ge-empty">Couldn't load this step's fields.</p>
          )}
        </section>
      </div>
    </ExpressionPreviewContext.Provider>
  )
}

export default InspectorSetup
