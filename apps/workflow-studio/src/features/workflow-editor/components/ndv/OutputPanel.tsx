import { memo, useState } from 'react';
import { Database, Code, Pin, Clock, ArrowUp, Zap, Globe, GitBranch } from 'lucide-react';
import type { NodeExecutionData } from '../../types/workflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import RunDataDisplay from './RunDataDisplay';

interface OutputPanelProps {
  nodeId: string;
  executionData: NodeExecutionData | null;
}

type DisplayMode = 'json' | 'schema';

const OutputPanel = memo(function OutputPanel({ nodeId, executionData }: OutputPanelProps) {
  const [displayMode, setDisplayMode] = useState<DisplayMode>('schema');

  const hasPinned = useWorkflowStore((s) => s.hasPinnedData(nodeId));
  const getPinnedDataForDisplay = useWorkflowStore((s) => s.getPinnedDataForDisplay);
  const pinNodeData = useWorkflowStore((s) => s.pinNodeData);
  const unpinNodeData = useWorkflowStore((s) => s.unpinNodeData);

  const isPinned = hasPinned;

  // Use pinned data if available, otherwise use execution data
  // getPinnedDataForDisplay unwraps { json: {...} } to just {...}
  const displayData = isPinned
    ? getPinnedDataForDisplay(nodeId)
    : executionData?.output?.items;

  const handlePinToggle = () => {
    if (isPinned) {
      unpinNodeData(nodeId);
    } else if (executionData?.output?.items && executionData.output.items.length > 0) {
      // Convert to backend format: { json: {...} }[]
      const backendFormat = executionData.output.items.map((item) => ({
        json: item as Record<string, unknown>,
      }));
      pinNodeData(nodeId, backendFormat);
    }
  };

  const hasData = executionData?.output?.items && executionData.output.items.length > 0;
  const hasError = executionData?.output?.error;
  const itemCount = executionData?.output?.items?.length ?? 0;
  const metrics = executionData?.metrics;

  // Use server-accurate execution time
  const executionTime = metrics?.executionTimeMs != null
    ? metrics.executionTimeMs < 1000
      ? `${Math.round(metrics.executionTimeMs)}ms`
      : `${(metrics.executionTimeMs / 1000).toFixed(2)}s`
    : null;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border bg-card px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-6 h-6 rounded bg-[var(--success)]/10 flex items-center justify-center">
            <ArrowUp size={12} className="text-[var(--success)]" />
          </div>
          <span className="text-[13px] font-semibold text-foreground">Output</span>
          {hasData && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-medium bg-[var(--success)]/10 text-[var(--success)]">
              {itemCount} items
            </span>
          )}
          {hasError && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-medium bg-destructive/10 text-destructive">
              Error
            </span>
          )}
          {executionTime && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Clock size={11} />
              {executionTime}
            </span>
          )}
          {/* LLM token badge */}
          {metrics?.totalTokens != null && (
            <span className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium bg-purple-500/10 text-purple-500" title={`In: ${metrics.inputTokens ?? 0} / Out: ${metrics.outputTokens ?? 0}`}>
              <Zap size={10} />
              {metrics.totalTokens.toLocaleString()} tok
            </span>
          )}
          {/* HTTP status badge */}
          {metrics?.responseStatusCode != null && (
            <span className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${
              metrics.responseStatusCode < 400
                ? 'bg-[var(--success)]/10 text-[var(--success)]'
                : 'bg-destructive/10 text-destructive'
            }`}>
              <Globe size={10} />
              {metrics.responseStatusCode}
            </span>
          )}
          {/* Branch decision badge */}
          {metrics?.branchDecision != null && (
            <span className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium bg-blue-500/10 text-blue-500">
              <GitBranch size={10} />
              {metrics.branchDecision}
            </span>
          )}
          {isPinned && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-medium bg-[var(--warning)]/10 text-[var(--warning)]">
              Pinned
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {/* Pin button */}
          <button
            onClick={handlePinToggle}
            disabled={!hasData && !isPinned}
            className={`rounded-md p-1.5 transition-colors ${
              isPinned
                ? 'bg-[var(--warning)]/10 text-[var(--warning)]'
                : 'text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed'
            }`}
            title={isPinned ? 'Unpin data' : 'Pin data'}
          >
            <Pin size={13} />
          </button>

          {/* Display mode toggle */}
          <div className="bg-muted rounded-md p-0.5 flex items-center">
            <button
              onClick={() => setDisplayMode('schema')}
              className={`rounded p-1.5 transition-colors ${
                displayMode === 'schema'
                  ? 'bg-card shadow-xs text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title="Schema view"
            >
              <Database size={13} />
            </button>
            <button
              onClick={() => setDisplayMode('json')}
              className={`rounded p-1.5 transition-colors ${
                displayMode === 'json'
                  ? 'bg-card shadow-xs text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title="JSON view"
            >
              <Code size={13} />
            </button>
          </div>
        </div>
      </div>

      {/* Data display */}
      <div className="flex-1 overflow-auto p-3">
        {!executionData ? (
          <div className="flex h-full flex-col items-center justify-center text-center px-6">
            <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center mb-3">
              <Database size={18} className="text-muted-foreground/50" />
            </div>
            <p className="text-[13px] font-medium text-foreground mb-0.5">
              No output data yet
            </p>
            <p className="text-[12px] text-muted-foreground">
              Run the workflow to see results
            </p>
          </div>
        ) : executionData.status === 'running' ? (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-primary" />
            <p className="mt-2 text-[13px] font-medium text-foreground">Executing...</p>
          </div>
        ) : hasError ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
            <p className="text-[13px] font-semibold text-destructive">Error</p>
            <p className="mt-1 text-[12px] text-destructive/80">
              {executionData.output?.error}
            </p>
          </div>
        ) : displayData && displayData.length > 0 ? (
          <RunDataDisplay
            data={displayData}
            mode={displayMode}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center px-6">
            <div className="w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center mb-3">
              <Database size={18} className="text-muted-foreground/50" />
            </div>
            <p className="text-[13px] font-medium text-foreground mb-0.5">
              No output items
            </p>
            <p className="text-[12px] text-muted-foreground">
              The node executed but returned no data
            </p>
          </div>
        )}
      </div>

    </div>
  );
});

export default OutputPanel;
