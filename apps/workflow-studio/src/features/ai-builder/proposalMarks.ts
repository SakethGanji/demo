/**
 * Maps a proposal's `diff` (§4.9) to what the canvas draws: historyStore `DiffMark`s keyed by node id (= node name)
 * and faded ghost nodes for removed steps. When the server sent no diff, the marks are computed from the two
 * definitions with the editor's own `lib/workflowDiff` so the two views agree.
 */
import type { Node } from '@xyflow/react';
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes';
import type { DiffMark } from '@/features/workflow-editor/stores/historyStore';
import type { WorkflowNodeData } from '@/features/workflow-editor/types/workflow';
import { diffWorkflows } from '@/features/workflow-editor/lib/workflowDiff';
import { backendToDefinition } from '@/features/workflow-editor/lib/versionDiffView';
import { fromBackendWorkflow } from '@/features/workflow-editor/lib/workflowTransform';
import type { ProposalDiff, ProposedDefinition } from './types';

type BackendDefinition = ApiWorkflowDetail['definition'];

export interface DiffItem {
  name: string;
  kind: DiffMark;
  /** changed only: which parts changed, as the engine names them (`parameters.url`, `on_error`, ...) */
  fields: string[];
}

/** Ghosts are never measured by React Flow, so they need a size (same as the version-history view). */
const GHOST_SIZE = { width: 64, height: 87 };

/** Added, then changed, then removed: the order a reviewer reads them in. */
export function diffItems(diff: ProposalDiff | null | undefined): DiffItem[] {
  if (!diff) return [];
  return [
    ...diff.nodes.added.map((name): DiffItem => ({ name, kind: 'added', fields: [] })),
    ...diff.nodes.changed.map((c): DiffItem => ({ name: c.name, kind: 'changed', fields: c.fields })),
    ...diff.nodes.removed.map((name): DiffItem => ({ name, kind: 'removed', fields: [] })),
  ];
}

export function marksFromDiff(diff: ProposalDiff | null | undefined): Record<string, DiffMark> {
  const marks: Record<string, DiffMark> = {};
  for (const it of diffItems(diff)) marks[it.name] = it.kind;
  return marks;
}

/** The same marks from two definitions, through the editor's `diffWorkflows` (used when the proposal has no diff). */
export function marksFromDefinitions(base: BackendDefinition | null, proposed: ProposedDefinition | null): Record<string, DiffMark> {
  if (!proposed) return {};
  const target = backendToDefinition(proposed as unknown as BackendDefinition);
  const before = base ? backendToDefinition(base) : { nodes: [], connections: [] };
  const d = diffWorkflows(before, target);
  const marks: Record<string, DiffMark> = {};
  for (const n of d.added) marks[n] = 'added';
  for (const n of d.changed) marks[n] = 'changed';
  for (const n of d.removed) marks[n] = 'removed';
  return marks;
}

/** Faded stand-ins for the steps the change removes, drawn where they were. Needs the definition they came from. */
export function ghostNodesFor(removed: string[], base: BackendDefinition | null): Node<WorkflowNodeData>[] {
  if (!removed.length || !base) return [];
  const names = new Set(removed);
  return fromBackendWorkflow({ id: '', name: '', active: false, definition: base } as ApiWorkflowDetail).nodes
    .filter((n) => names.has(n.data.name))
    .map((n) => ({
      ...n,
      draggable: false, selectable: false, connectable: false, deletable: false, focusable: false,
      zIndex: -1, measured: GHOST_SIZE,
    }));
}

/** One line for the review header: "2 steps added, 1 changed". */
export function diffSummaryText(diff: ProposalDiff | null | undefined): string {
  if (!diff) return 'No changes to show.';
  const { added, changed, removed } = diff.counts;
  const parts: string[] = [];
  const steps = (n: number) => `${n} step${n === 1 ? '' : 's'}`;
  if (added) parts.push(`${steps(added)} added`);
  if (changed) parts.push(`${changed} changed`);
  if (removed) parts.push(`${removed} removed`);
  return parts.length ? parts.join(', ') : 'No steps changed.';
}

/** Plain-English name of a changed part, for "what changed" lines. */
export function fieldLabel(field: string): string {
  if (field.startsWith('parameters.')) return `its ${field.slice('parameters.'.length).replace(/[_.]/g, ' ')} setting`;
  switch (field) {
    case 'parameters': return 'its settings';
    case 'type': return 'what kind of step it is';
    case 'on_error': return 'what happens when it fails';
    case 'disabled': return 'whether it is switched off';
    case 'label': return 'its label';
    default: return field.replace(/[_.]/g, ' ');
  }
}
