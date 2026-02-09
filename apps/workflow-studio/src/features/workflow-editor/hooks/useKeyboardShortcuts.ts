import { useEffect, useCallback, useState } from 'react';
import { useReactFlow } from 'reactflow';
import { useWorkflowStore } from '../stores/workflowStore';
import { useEditorLayoutStore } from '../stores/editorLayoutStore';
import { useNDVStore } from '../stores/ndvStore';

interface KeyboardShortcutsOptions {
  onSave?: () => void;
  onSaveAs?: () => void;
}

export function useKeyboardShortcuts(options: KeyboardShortcutsOptions = {}) {
  const { onSave, onSaveAs } = options;
  const { fitView, zoomIn, zoomOut, getNodes, setNodes } = useReactFlow();
  const [isShortcutsHelpOpen, setIsShortcutsHelpOpen] = useState(false);

  const deleteNode = useWorkflowStore((s) => s.deleteNode);
  const deleteNodes = useWorkflowStore((s) => s.deleteNodes);
  const selectedNodeId = useWorkflowStore((s) => s.selectedNodeId);
  const addStickyNote = useWorkflowStore((s) => s.addStickyNote);
  const copyNodes = useWorkflowStore((s) => s.copyNodes);
  const cutNodes = useWorkflowStore((s) => s.cutNodes);
  const pasteNodes = useWorkflowStore((s) => s.pasteNodes);
  const duplicateNodes = useWorkflowStore((s) => s.duplicateNodes);
  const undo = useWorkflowStore((s) => s.undo);
  const redo = useWorkflowStore((s) => s.redo);
  const moveNodes = useWorkflowStore((s) => s.moveNodes);
  const clipboard = useWorkflowStore((s) => s.clipboard);
  const exportWorkflow = useWorkflowStore((s) => s.exportWorkflow);
  const importWorkflow = useWorkflowStore((s) => s.importWorkflow);
  const workflowName = useWorkflowStore((s) => s.workflowName);

  const closePanel = useEditorLayoutStore((s) => s.closeCreatorPanel);
  const openPanel = useEditorLayoutStore((s) => s.openCreatorPanel);

  const closeNDV = useNDVStore((s) => s.closeNDV);
  const openNDV = useNDVStore((s) => s.openNDV);
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
          closeNDV();
          event.preventDefault();
          return;
        }
        // Reset node creator context (connection/subnode mode)
        closePanel();
      }

      // Don't handle other shortcuts when typing
      if (isInputFocused) return;

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
        const json = exportWorkflow();
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${workflowName.replace(/[^a-z0-9]/gi, '_')}.json`;
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
                importWorkflow(json);
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
        undo();
        return;
      }

      // Ctrl/Cmd + Shift + Z or Ctrl/Cmd + Y: Redo
      if ((modifierKey && event.shiftKey && event.key === 'Z') ||
          (modifierKey && event.key === 'y')) {
        event.preventDefault();
        redo();
        return;
      }

      // Ctrl/Cmd + C: Copy
      if (modifierKey && event.key === 'c') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
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
        openPanel('regular');
        return;
      }

      // T: Add trigger node
      if (event.key === 't' && !modifierKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        openPanel('trigger');
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
        addStickyNote({ x: centerX + 100, y: centerY - 100 });
        return;
      }

      // Delete/Backspace: Delete selected node(s)
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        const nodes = getNodes();
        const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
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
        const nodeIds = selectedIds.length > 0 ? selectedIds : (selectedNodeId ? [selectedNodeId] : []);

        if (nodeIds.length > 0) {
          event.preventDefault();
          const step = event.shiftKey ? 50 : 10; // Shift for larger movements
          const delta = {
            x: event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0,
            y: event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0,
          };
          moveNodes(nodeIds, delta);
        }
        return;
      }

      // Enter: Open selected node details
      if (event.key === 'Enter' && selectedNodeId) {
        event.preventDefault();
        openNDV(selectedNodeId);
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
      deleteNode,
      deleteNodes,
      selectedNodeId,
      closePanel,
      openPanel,
      closeNDV,
      isNDVOpen,
      addStickyNote,
      getNodes,
      setNodes,
      copyNodes,
      cutNodes,
      pasteNodes,
      duplicateNodes,
      undo,
      redo,
      moveNodes,
      clipboard,
      openNDV,
      exportWorkflow,
      importWorkflow,
      workflowName,
    ]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return {
    shortcuts: [
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
    ],
    isShortcutsHelpOpen,
    setIsShortcutsHelpOpen,
  };
}
