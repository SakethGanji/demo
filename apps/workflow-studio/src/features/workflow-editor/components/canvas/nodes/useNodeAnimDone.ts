import { useCallback } from 'react'
import { useUpdateNodeInternals } from '@xyflow/react'
import { useStageStore, type StageNodeData } from '@/features/ai-builder/stage/stageStore'
import type { AnimState } from '@/features/ai-builder/stage/buildOps'

/** Finishes a node animation: `anim` goes back to idle (only if it is still the one that ran), then re-measure. */
export function useNodeAnimDone(id: string) {
  const updateNodeInternals = useUpdateNodeInternals()
  return useCallback(
    (expected: AnimState) => {
      const st = useStageStore.getState()
      const node = st.nodes.find((n) => n.id === id)
      if (!node || (node.data as StageNodeData).anim !== expected) return
      st.patchNodeData(id, { anim: 'idle' })
      updateNodeInternals(id)
    },
    [id, updateNodeInternals],
  )
}
