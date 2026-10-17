import { History, Pin } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/shared/components/ui/button';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNDVStore } from '../../stores/ndvStore';
import { useBackToLive } from '../../hooks/useViewingRun';

export default function ViewingRunBanner() {
  const viewing = useWorkflowStore((s) => s.viewingExecution);
  const backToLive = useBackToLive();
  if (!viewing) return null;

  const pinSelected = () => {
    const { selectedNodeId, executionData, pinNodeData } = useWorkflowStore.getState();
    const ndv = useNDVStore.getState();
    const nodeId = (ndv.isOpen ? ndv.activeNodeId : null) ?? selectedNodeId;
    const items = nodeId ? executionData[nodeId]?.output?.items : undefined;
    if (!nodeId || !items) {
      toast.info('Select a node that ran in this run to pin its output');
      return;
    }
    pinNodeData(nodeId, items.map((json) => ({ json })));
    toast.success('Pinned this node’s output');
  };

  return (
    <div
      className="glass-overlay flex h-10 max-w-[calc(100%-24px)] items-center gap-3 rounded-panel pr-2 pl-3 text-[12.5px]"
      data-testid="viewing-run-banner"
      data-status={viewing.status}
    >
      <History size={14} className="shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 truncate" title={viewing.id}>
        Viewing run <span className="font-mono">#{viewing.id}</span> from {new Date(viewing.startTime).toLocaleString()}
      </span>
      <Button variant="outline" size="xs" onClick={pinSelected} data-testid="viewing-run-pin">
        <Pin /> Pin this data
      </Button>
      <Button variant="secondary" size="xs" onClick={backToLive} data-testid="viewing-run-back-to-live">
        Back to live
      </Button>
    </div>
  );
}
