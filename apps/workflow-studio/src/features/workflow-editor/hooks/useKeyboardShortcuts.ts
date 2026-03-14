import { useEffect, useCallback, useState } from 'react';
import { useReactFlow } from '@xyflow/react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useEditorLayoutStore } from '../stores/editorLayoutStore';
import { useNDVStore } from '../stores/ndvStore';

interface KeyboardShortcutsOptions {
  onSave?: () => void;
  onSaveAs?: () => void;
}

const SHORTCUTS = [
  // Edit operations
  { key: 'Ctrl/Cmd + Z', description: 'Undo' },
  { key: 'Ctrl/Cmd + Shift + Z', description: 'Redo' },
  { key: 'Ctrl/Cmd + C', description: 'Copy selected nodes' },
  { key: 'Ctrl/Cmd + X', description: 'Cut selected nodes' },
  { key: 'Ctrl/Cmd + V', description: 'Paste nodes' },
  { key: 'Ctrl/Cmd + D', description: 'Duplicate selected nodes' },
  { key: 'Ctrl/Cmd + A', description: 'Select all nodes' },
  { key: 'Shift + Click', description: 'Add node to selection' },
  { key: 'Ctrl + Drag', description: 'Selection box' },
  { key: 'Delete/Backspace', description: 'Delete selected nodes' },
  // Node operations
  { key: 'Enter', description: 'Open selected node settings' },
  { key: 'Arrow keys', description: 'Move selected nodes' },
  { key: 'Shift + Arrow', description: 'Move nodes faster' },
  // Add nodes
  { key: 'N', description: 'Add new node' },
  { key: 'T', description: 'Add trigger node' },
  { key: 'S', description: 'Add sticky note' },
  // File operations
  { key: 'Ctrl/Cmd + S', description: 'Save workflow' },
  { key: 'Ctrl/Cmd + E', description: 'Export workflow' },
  { key: 'Ctrl/Cmd + I', description: 'Import workflow' },
  // View operations
  { key: 'Ctrl/Cmd + 0', description: 'Fit to view' },
  { key: 'Ctrl/Cmd + F', description: 'Zoom to selection' },
  { key: 'Ctrl/Cmd + +', description: 'Zoom in' },
  { key: 'Ctrl/Cmd + -', description: 'Zoom out' },
  { key: 'F', description: 'Fit to view' },
  { key: 'Escape', description: 'Close panel/modal' },
  { key: '?', description: 'Show shortcuts help' },
] as const;

export function useKeyboardShortcuts(options: KeyboardShortcutsOptions = {}) {
  const { onSave, onSaveAs } = options;
  const { fitView, zoomIn, zoomOut, getNodes, setNodes } = useReactFlow();
  const [isShortcutsHelpOpen, setIsShortcutsHelpOpen] = useState(false);

  const isNDVOpen = useNDVStore((s) => s.isOpen);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const isInputFocused =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable;

      // Always handle Escape
      if (event.key === 'Escape') {
        if (isNDVOpen) {
          useNDVStore.getState().closeNDV();
          event.preventDefault();
          return;
        }
        // Reset node creator context (connection mode)
        useEditorLayoutStore.getState().closeCreatorPanel();
      }

      // Don't handle other shortcuts when typing or when NDV modal is open
      // (allow native copy/paste/cut in inputs, code editors, and NDV content)
      if (isInputFocused) return;
      if (isNDVOpen) return;

      const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
      const modifierKey = isMac ? event.metaKey : event.ctrlKey;

      // Ctrl/Cmd + S: Save workflow
      if (modifierKey && event.key === 's') {
        event.preventDefault();
        onSave?.();
        return;
      }

      // Ctrl/Cmd + Shift + S: Save as
      if (modifierKey && event.shiftKey && event.key === 'S') {
        event.preventDefault();
        onSaveAs?.();
        return;
      }

      // Ctrl/Cmd + E: Export workflow
      if (modifierKey && event.key === 'e') {
        event.preventDefault();
        const wfStore = useWorkflowStore.getState();
        const json = wfStore.exportWorkflow();
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${wfStore.workflowName.replace(/[^a-z0-9]/gi, '_')}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        return;
      }

      // Ctrl/Cmd + I: Import workflow
      if (modifierKey && event.key === 'i') {
        event.preventDefault();
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = (e) => {
          const file = (e.target as HTMLInputElement).files?.[0];
          if (file) {
            const reader = new FileReader();
            reader.onload = (e) => {
              const json = e.target?.result as string;
              if (json) {
                useWorkflowStore.getState().importWorkflow(json);
              }
            };
            reader.readAsText(file);
          }
        };
        input.click();
        return;
      }

      // Ctrl/Cmd + Z: Undo
      if (modifierKey && !event.shiftKey && event.key === 'z') {
        event.preventDefault();
        useWorkflowStore.getState().undo();
        return;
      }

      // Ctrl/Cmd + Shift + Z or Ctrl/Cmd + Y: Redo
      if ((modifierKey && event.shiftKey && event.key === 'Z') ||
          (modifierKey && event.key === 'y')) {
        event.preventDefault();
        useWorkflowStore.getState().redo();
        return;
      }

      // Ctrl/Cmd + C: Copy
      if (modifierKey && event.key === 'c') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
        const { selectedNodeId, copyNodes } = useWorkflowStore.getState();
        if (selectedIds.length > 0) {
          copyNodes(selectedIds);
        } else if (selectedNodeId) {
          copyNodes([selectedNodeId]);
        }
        return;
      }

      // Ctrl/Cmd + X: Cut
      if (modifierKey && event.key === 'x') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
        const { selectedNodeId, cutNodes } = useWorkflowStore.getState();
        if (selectedIds.length > 0) {
          cutNodes(selectedIds);
        } else if (selectedNodeId) {
          cutNodes([selectedNodeId]);
        }
        return;
      }

      // Ctrl/Cmd + V: Paste
      if (modifierKey && event.key === 'v') {
        event.preventDefault();
        const { clipboard, pasteNodes } = useWorkflowStore.getState();
        if (clipboard) {
          pasteNodes();
        }
        return;
      }

      // Ctrl/Cmd + D: Duplicate
      if (modifierKey && event.key === 'd') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
        const { selectedNodeId, duplicateNodes } = useWorkflowStore.getState();
        if (selectedIds.length > 0) {
          duplicateNodes(selectedIds);
        } else if (selectedNodeId) {
          duplicateNodes([selectedNodeId]);
        }
        return;
      }

      // Ctrl/Cmd + 0: Fit view
      if (modifierKey && event.key === '0') {
        event.preventDefault();
        fitView({ padding: 0.2, duration: 200 });
        return;
      }

      // Ctrl/Cmd + =: Zoom in
      if (modifierKey && (event.key === '=' || event.key === '+')) {
        event.preventDefault();
        zoomIn({ duration: 200 });
        return;
      }

      // Ctrl/Cmd + -: Zoom out
      if (modifierKey && event.key === '-') {
        event.preventDefault();
        zoomOut({ duration: 200 });
        return;
      }

      // Ctrl/Cmd + A: Select all nodes
      if (modifierKey && event.key === 'a') {
        event.preventDefault();
        const nodes = getNodes();
        const selectableNodes = nodes.filter(
          (n) => n.type !== 'addNodes' && n.type !== 'stickyNote'
        );
        setNodes(
          nodes.map((n) => ({
            ...n,
            selected: selectableNodes.some((sn) => sn.id === n.id),
          }))
        );
        return;
      }

      // N: Add new node (when no modifiers)
      if (event.key === 'n' && !modifierKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        useEditorLayoutStore.getState().openCreatorPanel('regular');
        return;
      }

      // T: Add trigger node
      if (event.key === 't' && !modifierKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        useEditorLayoutStore.getState().openCreatorPanel('trigger');
        return;
      }

      // S: Add sticky note
      if (event.key === 's' && !modifierKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        // Get center of viewport for sticky note placement
        const nodes = getNodes();
        const centerX = nodes.length > 0
          ? nodes.reduce((sum, n) => sum + n.position.x, 0) / nodes.length
          : 400;
        const centerY = nodes.length > 0
          ? nodes.reduce((sum, n) => sum + n.position.y, 0) / nodes.length
          : 300;
        useWorkflowStore.getState().addStickyNote({ x: centerX + 100, y: centerY - 100 });
        return;
      }

      // Delete/Backspace: Delete selected node(s)
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
        const { selectedNodeId, deleteNodes, deleteNode } = useWorkflowStore.getState();
        if (selectedIds.length > 0) {
          deleteNodes(selectedIds);
        } else if (selectedNodeId) {
          deleteNode(selectedNodeId);
        }
        return;
      }

      // Arrow keys: Move selected nodes
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
        const { selectedNodeId } = useWorkflowStore.getState();
        const nodeIds = selectedIds.length > 0 ? selectedIds : (selectedNodeId ? [selectedNodeId] : []);

        if (nodeIds.length > 0) {
          event.preventDefault();
          const step = event.shiftKey ? 50 : 10; // Shift for larger movements
          const delta = {
            x: event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0,
            y: event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0,
          };
          useWorkflowStore.getState().moveNodes(nodeIds, delta);
        }
        return;
      }

      // Enter: Open selected node details
      if (event.key === 'Enter') {
        const { selectedNodeId } = useWorkflowStore.getState();
        if (selectedNodeId) {
          event.preventDefault();
          useNDVStore.getState().openNDV(selectedNodeId);
        }
        return;
      }

      // F: Fit view
      if (event.key === 'f' && !modifierKey) {
        event.preventDefault();
        fitView({ padding: 0.2, duration: 200 });
        return;
      }

      // Ctrl/Cmd + F: Zoom to selection
      if (modifierKey && event.key === 'f') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
        const { selectedNodeId } = useWorkflowStore.getState();
        if (selectedIds.length > 0) {
          fitView({ padding: 0.3, duration: 200, nodes: nodes.filter((n) => n.selected) });
        } else if (selectedNodeId) {
          const selectedNode = nodes.find((n) => n.id === selectedNodeId);
          if (selectedNode) {
            fitView({ padding: 0.5, duration: 200, nodes: [selectedNode] });
          }
        }
        return;
      }

      // Space + drag: Pan (handled by ReactFlow, but we can show hints)

      // ?: Show keyboard shortcuts help (Shift + /)
      if (event.shiftKey && event.key === '?') {
        event.preventDefault();
        setIsShortcutsHelpOpen(true);
        return;
      }
    },
    [
      onSave,
      onSaveAs,
      fitView,
      zoomIn,
      zoomOut,
      getNodes,
      setNodes,
      isNDVOpen,
    ]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return {
    shortcuts: SHORTCUTS,
    isShortcutsHelpOpen,
    setIsShortcutsHelpOpen,
  };
}
