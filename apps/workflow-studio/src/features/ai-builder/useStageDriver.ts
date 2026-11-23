/**
 * Drives the stage from the sidebar's state (PLAN-MOTION-UI §2.2). The player (`useBuildPlayer`, mounted by the canvas)
 * owns playback and the move to `review`; this hook only starts and ends the stage and draws the blueprint.
 *
 *   thinking -> stage begins (draft dims, the AI marker appears)
 *   plan     -> blueprint outlines for the planned steps
 *   building -> mode `building`; rounds play as the events arrive
 *   review   -> a proposal opened cold is shown as the player's final frame
 *   ask      -> the stage ends (a request that failed, or a fresh start)
 *
 * An app (CONTRACTS-CAPGRAPH §5) is many proposals, one per part. While the app panel is open (`appMode`), the canvas follows the
 * part whose proposal is in the store (`proposal.app_build_id` is set): the same states drive it. With no part on screen the
 * proposal in the store is the request the app replaced, which is not a plan to draw, so the stage ends.
 */
import { useEffect } from 'react';
import type { BuildState } from './lib/buildState';
import { endStage, ensureStage } from './lib/stageBridge';
import { blueprintNodes, isBlueprintId } from './stage/blueprint';
import { useStageStore, type StageNode } from './stage/stageStore';
import type { Proposal, Spec } from './types';

export function useStageDriver({ state: rawState, proposal, spec, loading, appMode }: {
  state: BuildState;
  proposal: Proposal | null;
  spec: Spec | null;
  /** a proposal is being fetched: do not treat the empty panel as "ask" */
  loading: boolean;
  /** the app panel is open: only a part's proposal (one with `app_build_id`) is drawn */
  appMode?: boolean;
}) {
  const state: BuildState = appMode && !proposal?.app_build_id ? 'ask' : rawState;
  const pid = proposal?.id ?? 'pending';
  const hasDefinition = !!proposal?.proposed_definition;
  const stepKey = state === 'plan' && spec ? spec.steps.map((s) => `${s.name}\u0000${s.node_type}`).join('\u0001') : '';

  useEffect(() => {
    switch (state) {
      case 'ask':
        if (!loading && useStageStore.getState().mode !== 'settling') endStage();
        return;
      case 'thinking':
        ensureStage(pid, 'blueprint');
        useStageStore.getState().setPresence({ visible: true });
        return;
      case 'plan':
        ensureStage(pid, 'blueprint');
        useStageStore.getState().setPresence({ visible: true });
        return;
      case 'building':
        ensureStage(pid, 'building');
        useStageStore.getState().setPresence({ visible: true });
        return;
      case 'review':
        if (hasDefinition) ensureStage(pid, 'review');
        else endStage(); // a failed build with nothing to show
        return;
      case 'discarded':
      case 'undone':
        if (useStageStore.getState().mode !== 'settling') endStage();
        return;
      default:
        return; // kept: `keep()` hands the canvas back itself
    }
  }, [state, pid, hasDefinition, loading]);

  // blueprint outlines follow the plan while it is being checked (and are redrawn if the person edits the steps)
  useEffect(() => {
    if (state !== 'plan' || !spec) return;
    const st = useStageStore.getState();
    // once the player has begun, the outlines belong to it: the nodes on stage are the build's own, not a draft to place around
    if (!st.active || st.playback !== 'idle') return;
    st.setNodes((ns) => {
      const real = ns.filter((n) => !isBlueprintId(n.id));
      const bp = blueprintNodes(spec.steps, real.map((n) => ({ id: n.id, position: n.position }))) as unknown as StageNode[];
      return [...real, ...bp];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `stepKey` is the identity of the steps
  }, [state, stepKey, pid]);
}
