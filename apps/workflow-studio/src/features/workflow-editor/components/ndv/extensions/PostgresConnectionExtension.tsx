import { Database } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData } from '../../../types/workflow';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { useEditorProjectStore } from '../../../stores/editorProjectStore';
import { useConnections } from '@/features/connections/useConnections';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';

const NONE = '__connection_string__';

/**
 * Postgres: "Connection" picks one of the project's database connections (parameter `connection` = its key) as an
 * alternative to typing a connection string. Renders nothing when the project has no connections and none is set.
 */
export default function PostgresConnectionExtension({ node }: { node: Node<WorkflowNodeData> }) {
  const pid = useEditorProjectStore((s) => s.projectId);
  const { data } = useConnections(pid);
  const items = data ?? [];
  const current = ((node.data.parameters ?? {}) as Record<string, unknown>).connection;
  const key = typeof current === 'string' ? current : '';
  if (!items.length && !key) return null;

  const labels: Record<string, string> = { [NONE]: 'Use a connection string', ...Object.fromEntries(items.map((c) => [c.key, c.name])) };
  if (key && !items.some((c) => c.key === key)) labels[key] = `${key} (not found)`;

  const set = (v: string) => {
    const store = useWorkflowStore.getState();
    const cur = store.nodes.find((n) => n.id === node.id);
    if (!cur) return;
    const next = { ...((cur.data as WorkflowNodeData).parameters as Record<string, unknown>) };
    if (v === NONE) delete next.connection;
    else next.connection = v;
    store.updateNodeData(node.id, { parameters: next });
  };

  return (
    <div className="tile" data-testid="postgres-connection">
      <div className="px-3 py-2">
        <div className="mb-2 flex items-center gap-2">
          <Database size={14} className="text-muted-foreground" />
          <span className="text-sm font-medium text-foreground">Connection</span>
        </div>
        <Select value={key || NONE} onValueChange={(v) => set((v as string) || NONE)} items={labels}>
          <SelectTrigger className="w-full" data-testid="postgres-connection-select">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>Use a connection string</SelectItem>
            {items.map((c) => (
              <SelectItem key={c.id} value={c.key}>
                <span className="min-w-0 truncate">{c.name}</span>
                <span className="ml-auto pl-3 text-[11.5px] text-muted-foreground">{c.database ?? c.host}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="mt-2 text-xs text-muted-foreground">
          {key ? 'Uses the project connection; the connection string below is ignored.' : 'Or pick a database connected on the project page.'}
        </p>
      </div>
    </div>
  );
}
