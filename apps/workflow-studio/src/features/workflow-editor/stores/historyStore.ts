/**
 * Version-history sheet state and the on-canvas diff (`data-diff` on nodes).
 * `marks` are keyed by node id (= node name); `ghostNodes` are removed nodes drawn faded.
 * `edgeMarks` are keyed by edge id: a removed edge is drawn as a dashed ghost (AI stage; empty for version history).
 */
import { create } from 'zustand';
import type { Node } from '@xyflow/react';

export type DiffMark = 'added' | 'removed' | 'changed';

interface HistoryState {
  open: boolean;
  selectedVersion: number | null;
  marks: Record<string, DiffMark>;
  edgeMarks: Record<string, DiffMark>;
  ghostNodes: Node[];
  setOpen: (open: boolean) => void;
  selectVersion: (version: number | null) => void;
  setDiff: (marks: Record<string, DiffMark>, ghostNodes: Node[], edgeMarks?: Record<string, DiffMark>) => void;
  clearDiff: () => void;
}

export const useHistoryStore = create<HistoryState>((set) => ({
  open: false,
  selectedVersion: null,
  marks: {},
  edgeMarks: {},
  ghostNodes: [],
  setOpen: (open) => set(open ? { open } : { open, selectedVersion: null, marks: {}, edgeMarks: {}, ghostNodes: [] }),
  selectVersion: (version) => set({ selectedVersion: version, marks: {}, edgeMarks: {}, ghostNodes: [] }),
  setDiff: (marks, ghostNodes, edgeMarks = {}) => set({ marks, ghostNodes, edgeMarks }),
  clearDiff: () => set({ selectedVersion: null, marks: {}, edgeMarks: {}, ghostNodes: [] }),
}));
