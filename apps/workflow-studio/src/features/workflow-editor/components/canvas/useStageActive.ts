import { useStageStore } from '@/features/ai-builder/stage/stageStore'

/**
 * True while an AI build or review owns the canvas. The run-status, live-check and test hooks use it to read as idle,
 * so results from the draft's last run never paint onto proposal nodes that share a name (PLAN-MOTION-UI §1.9).
 */
export function useStageActive(): boolean {
  return useStageStore((s) => s.active)
}
