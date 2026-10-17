/**
 * ⌘K palette state and a registry of editor actions. Components register the handlers they own
 * (`registerEditorAction('run', fn)`); the palette calls them. Node actions get the selected node id.
 */
import { create } from 'zustand';

export type EditorActionId =
  | 'add-node'
  | 'run'
  | 'test-step'
  | 'run-to'
  | 'run-from'
  | 'toggle-disable'
  | 'history'
  | 'tidy-up'
  | 'zoom-fit';

export type EditorActionHandler = (nodeId: string | null) => void;

/** Actions that need a selected node. */
export const NODE_ACTIONS: readonly EditorActionId[] = ['test-step', 'run-to', 'run-from', 'toggle-disable'];

const handlers = new Map<EditorActionId, EditorActionHandler>();

export function registerEditorAction(id: EditorActionId, handler: EditorActionHandler): () => void {
  handlers.set(id, handler);
  return () => {
    if (handlers.get(id) === handler) handlers.delete(id);
  };
}

export function getEditorAction(id: EditorActionId): EditorActionHandler | undefined {
  return handlers.get(id);
}

interface PaletteState {
  open: boolean;
  setOpen: (open: boolean) => void;
}

export const useEditorPaletteStore = create<PaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));
