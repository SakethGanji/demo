/**
 * Execution Logs Content
 *
 * Displays execution logs in a split view: node list + output preview.
 * Used inside the BottomPanel's Logs tab.
 */

import { useState, useEffect } from 'react';
import {
  Trash2,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  ScrollText,
} from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { cn } from '@/shared/lib/utils';

interface ExecutionLog {
  id: string;
  nodeName: string;
  nodeLabel: string;
  status: 'idle' | 'running' | 'success' | 'error';
  timestamp: number;
  duration?: number;
  itemCount?: number;
  error?: string;
}

export default function ExecutionLogsPanel() {
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);

  const executionData = useWorkflowStore((s) => s.executionData);
  const nodes = useWorkflowStore((s) => s.nodes);
  const clearExecutionData = useWorkflowStore((s) => s.clearExecutionData);

  // Convert execution data to logs format
  const logs: ExecutionLog[] = Object.entries(executionData)
    .map(([nodeId, data]): ExecutionLog | null => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node || node.type !== 'workflowNode') return null;

      return {
        id: nodeId,
        nodeName: node.data.name || nodeId,
        nodeLabel: node.data.label || node.data.name || 'Unknown',
        status: data.status,
        timestamp: data.startTime || Date.now(),
        duration:
          data.startTime && data.endTime
            ? data.endTime - data.startTime
            : undefined,
        itemCount: data.output?.items?.length,
        error: data.output?.error,
      };
    })
    .filter((log): log is ExecutionLog => log !== null)
    .sort((a, b) => a.timestamp - b.timestamp);

  const hasLogs = logs.length > 0;
  const isRunning = logs.some((l) => l.status === 'running');
  const hasErrors = logs.some((l) => l.status === 'error');
  const totalDuration = logs.reduce((sum, l) => sum + (l.duration || 0), 0);

  const selectedLog = selectedLogId
    ? logs.find((l) => l.id === selectedLogId)
    : null;
  const selectedNodeData = selectedLogId ? executionData[selectedLogId] : null;

  // Auto-select first log when execution starts
  useEffect(() => {
    if (hasLogs && !selectedLogId) {
      setSelectedLogId(logs[0]?.id || null);
    }
  }, [hasLogs, logs, selectedLogId]);

  const handleClear = () => {
    clearExecutionData();
    setSelectedLogId(null);
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header bar */}
      <div className="flex items-center justify-between h-8 px-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-foreground">Logs</span>
          {hasLogs && (
            <>
              <div className="w-px h-3 bg-border" />
              {isRunning ? (
                <span className="flex items-center gap-1 text-[10px] text-[var(--warning)]">
                  <Loader2 size={10} className="animate-spin" />
                  Running
                </span>
              ) : hasErrors ? (
                <span className="flex items-center gap-1 text-[10px] text-destructive">
                  <XCircle size={10} />
                  Failed
                </span>
              ) : (
                <span className="flex items-center gap-1 text-[10px] text-[var(--success)]">
                  <CheckCircle2 size={10} />
                  {totalDuration}ms
                </span>
              )}
            </>
          )}
        </div>
        <button
          onClick={handleClear}
          disabled={!hasLogs}
          className="p-1 text-muted-foreground hover:text-foreground hover:bg-accent rounded disabled:opacity-50 disabled:cursor-not-allowed"
          title="Clear logs"
        >
          <Trash2 size={12} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* Node list */}
        <div className="w-44 border-r border-border overflow-y-auto bg-muted/30 shrink-0">
          {!hasLogs ? (
            <div className="flex flex-col items-center justify-center h-full text-center p-3">
              <ScrollText size={18} className="text-muted-foreground/50 mb-2" />
              <p className="text-xs text-muted-foreground">No logs yet</p>
            </div>
          ) : (
            <div className="p-1.5 space-y-0.5">
              {logs.map((log) => (
                <button
                  key={log.id}
                  onClick={() => setSelectedLogId(log.id)}
                  className={cn(
                    'w-full flex items-center gap-2 px-2 py-1.5 rounded text-left transition-colors',
                    selectedLogId === log.id
                      ? 'bg-primary/10 text-primary'
                      : 'hover:bg-accent text-foreground'
                  )}
                >
                  {log.status === 'running' && (
                    <Loader2 size={12} className="animate-spin text-[var(--warning)] flex-shrink-0" />
                  )}
                  {log.status === 'success' && (
                    <CheckCircle2 size={12} className="text-[var(--success)] flex-shrink-0" />
                  )}
                  {log.status === 'error' && (
                    <XCircle size={12} className="text-destructive flex-shrink-0" />
                  )}
                  <span className="text-xs truncate flex-1">{log.nodeLabel}</span>
                  {log.duration !== undefined && (
                    <span className="text-[10px] text-muted-foreground flex-shrink-0">
                      {log.duration}ms
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Output preview */}
        <div className="flex-1 overflow-auto p-3">
          {selectedLog && selectedNodeData ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium text-foreground">
                    {selectedLog.nodeLabel}
                  </h3>
                  {selectedLog.status === 'success' && (
                    <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-[var(--success)]/10 text-[var(--success)]">
                      Success
                    </span>
                  )}
                  {selectedLog.status === 'error' && (
                    <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-destructive/10 text-destructive">
                      Error
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                  {selectedLog.duration !== undefined && (
                    <span className="flex items-center gap-1">
                      <Clock size={10} />
                      {selectedLog.duration}ms
                    </span>
                  )}
                  {selectedLog.itemCount !== undefined && (
                    <span>{selectedLog.itemCount} item{selectedLog.itemCount !== 1 && 's'}</span>
                  )}
                </div>
              </div>

              {selectedLog.error && (
                <div className="p-2 rounded-lg bg-destructive/10 border border-destructive/20">
                  <p className="text-xs text-destructive">{selectedLog.error}</p>
                </div>
              )}

              {selectedNodeData.output?.items &&
                selectedNodeData.output.items.length > 0 && (
                  <div className="space-y-1">
                    <h4 className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                      Output
                    </h4>
                    <pre className="p-2 rounded-lg bg-muted text-[11px] overflow-auto max-h-40 font-mono">
                      {JSON.stringify(selectedNodeData.output.items, null, 2)}
                    </pre>
                  </div>
                )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <p className="text-xs text-muted-foreground">
                {hasLogs
                  ? 'Select a node to view output'
                  : 'Run workflow to see logs'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
