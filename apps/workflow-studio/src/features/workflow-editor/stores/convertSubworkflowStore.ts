import { create } from 'zustand';

/**
 * State of the "Convert to sub-workflow" dialog: whether it is open, which nodes (by name) were selected when it
 * opened, and which nodes the dialog wants highlighted on the canvas (the ones a refusal names).
 */
interface ConvertSubworkflowState {
  open: boolean;
  nodeNames: string[];
  highlight: ReadonlySet<string>;
  openDialog: (nodeNames: string[]) => void;
  close: () => void;
  setHighlight: (names: string[]) => void;
}

const NONE: ReadonlySet<string> = new Set();

export const useConvertSubworkflowStore = create<ConvertSubworkflowState>((set) => ({
  open: false,
  nodeNames: [],
  highlight: NONE,
  openDialog: (nodeNames) => set({ open: true, nodeNames, highlight: NONE }),
  close: () => set({ open: false, highlight: NONE }),
  setHighlight: (names) => set({ highlight: names.length ? new Set(names) : NONE }),
}));

/** Names of the selected workflow nodes (not sticky notes), in canvas order. */
export function selectedWorkflowNodeNames(nodes: { type?: string; selected?: boolean; data?: unknown }[]): string[] {
  return nodes
    .filter((n) => n.type === 'workflowNode' && n.selected)
    .map((n) => (n.data as { name?: string } | undefined)?.name)
    .filter((n): n is string => typeof n === 'string');
}
