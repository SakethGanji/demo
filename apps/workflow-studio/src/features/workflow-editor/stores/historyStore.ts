/**
 * Version-history sheet state and the on-canvas diff (`data-diff` on nodes).
 * `marks` are keyed by node id (= node name); `ghostNodes` are removed nodes drawn faded.
 */
import { create } from 'zustand';
import type { Node } from '@xyflow/react';

export type DiffMark = 'added' | 'removed' | 'changed';

interface HistoryState {
  open: boolean;
  selectedVersion: number | null;
  marks: Record<string, DiffMark>;
  ghostNodes: Node[];
  setOpen: (open: boolean) => void;
  selectVersion: (version: number | null) => void;
  setDiff: (marks: Record<string, DiffMark>, ghostNodes: Node[]) => void;
  clearDiff: () => void;
}

export const useHistoryStore = create<HistoryState>((set) => ({
  open: false,
  selectedVersion: null,
  marks: {},
  ghostNodes: [],
  setOpen: (open) => set(open ? { open } : { open, selectedVersion: null, marks: {}, ghostNodes: [] }),
  selectVersion: (version) => set({ selectedVersion: version, marks: {}, ghostNodes: [] }),
  setDiff: (marks, ghostNodes) => set({ marks, ghostNodes }),
  clearDiff: () => set({ selectedVersion: null, marks: {}, ghostNodes: [] }),
}));
