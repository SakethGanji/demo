import { useEffect, useMemo } from 'react';
import { useReactFlow, type Node } from '@xyflow/react';
import { toast } from 'sonner';
import { History, LayoutGrid, Maximize, Play, Plus, PowerOff, Route, StepForward, FlaskConical } from 'lucide-react';
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from '@/shared/components/ui/command';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useHistoryStore } from '../../stores/historyStore';
import {
  NODE_ACTIONS,
  getEditorAction,
  registerEditorAction,
  useEditorPaletteStore,
  type EditorActionId,
} from '../../stores/editorActionsStore';
import { autoLayout } from '../../lib/autoLayout';
import { calculateNodeDimensions } from '../../lib/nodeStyles';
import type { WorkflowNodeData } from '../../types/workflow';

interface PaletteItem {
  id: EditorActionId;
  label: string;
  icon: typeof Play;
  group: 'Add' | 'Run' | 'Node' | 'View';
  shortcut?: string;
}

const ITEMS: PaletteItem[] = [
  { id: 'add-node', label: 'Add node', icon: Plus, group: 'Add', shortcut: 'N' },
  { id: 'run', label: 'Run workflow', icon: Play, group: 'Run', shortcut: '⌘↵' },
  { id: 'test-step', label: 'Test step', icon: FlaskConical, group: 'Run' },
  { id: 'run-to', label: 'Run to here', icon: Route, group: 'Run' },
  { id: 'run-from', label: 'Run from here', icon: StepForward, group: 'Run' },
  { id: 'toggle-disable', label: 'Disable or enable node', icon: PowerOff, group: 'Node', shortcut: 'D' },
  { id: 'history', label: 'Version history', icon: History, group: 'View' },
  { id: 'tidy-up', label: 'Tidy up', icon: LayoutGrid, group: 'View' },
  { id: 'zoom-fit', label: 'Zoom to fit', icon: Maximize, group: 'View', shortcut: 'F' },
];

const GROUPS: PaletteItem['group'][] = ['Add', 'Run', 'Node', 'View'];

/** Lay the workflow nodes out left to right and save one undo step. */
function tidyUp(nodes: Node[], edges: { source: string; target: string }[]) {
  const wf = nodes.filter((n) => n.type === 'workflowNode') as Node<WorkflowNodeData>[];
  if (wf.length === 0) return false;
  const layoutNodes = wf.map((n) => {
    const d = n.data;
    const dims = calculateNodeDimensions(
      Math.max(1, d.inputCount ?? d.inputs?.length ?? 1),
      Math.max(1, d.outputCount ?? d.outputs?.length ?? 1),
    );
    return { id: n.id, width: n.measured?.width ?? dims.width, height: n.measured?.height ?? dims.height };
  });
  const positions = autoLayout(layoutNodes, edges);
  const store = useWorkflowStore.getState();
  store.saveToHistory();
  store.onNodesChange(
    wf
      .filter((n) => positions[n.id])
      .map((n) => ({ type: 'position' as const, id: n.id, position: positions[n.id] })),
  );
  return true;
}

export default function EditorCommandPalette() {
  const open = useEditorPaletteStore((s) => s.open);
  const setOpen = useEditorPaletteStore((s) => s.setOpen);
  const selectedNodeId = useWorkflowStore((s) => s.selectedNodeId);
  const { fitView } = useReactFlow();

  // Actions this component owns; run/test/disable handlers are registered by whoever implements them.
  useEffect(() => {
    const offs = [
      registerEditorAction('add-node', () => useEditorLayoutStore.getState().openCreatorPanel('regular')),
      registerEditorAction('history', () => useHistoryStore.getState().setOpen(true)),
      registerEditorAction('tidy-up', () => {
        const { nodes, edges } = useWorkflowStore.getState();
        if (tidyUp(nodes, edges)) setTimeout(() => fitView({ padding: 0.2, duration: 200, maxZoom: 1 }), 50);
      }),
      registerEditorAction('zoom-fit', () => fitView({ padding: 0.2, duration: 200 })),
    ];
    return () => offs.forEach((off) => off());
  }, [fitView]);

  const grouped = useMemo(() => GROUPS.map((g) => [g, ITEMS.filter((i) => i.group === g)] as const), []);

  const run = (item: PaletteItem) => {
    setOpen(false);
    const handler = getEditorAction(item.id);
    if (!handler) {
      toast.info(`${item.label} is not available yet`);
      return;
    }
    // Let the dialog close (and restore focus) before the action opens another panel.
    setTimeout(() => handler(selectedNodeId), 0);
  };

  return (
    <CommandDialog open={open} onOpenChange={setOpen} title="Editor commands" description="Run an editor action" className="sm:max-w-md">
      <Command data-testid="command-palette">
        <CommandInput placeholder="Type a command…" data-testid="command-palette-input" />
        <CommandList className="max-h-[360px]">
          <CommandEmpty>No matching command.</CommandEmpty>
          {grouped.map(([group, items]) => (
            <CommandGroup key={group} heading={group}>
              {items.map((item) => {
                const needsNode = NODE_ACTIONS.includes(item.id);
                const Icon = item.icon;
                return (
                  <CommandItem
                    key={item.id}
                    value={`${item.label} ${item.id}`}
                    disabled={needsNode && !selectedNodeId}
                    onSelect={() => run(item)}
                    data-testid={`cmd-${item.id}`}
                  >
                    <Icon /> {item.label}
                    {item.shortcut && <CommandShortcut>{item.shortcut}</CommandShortcut>}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          ))}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
