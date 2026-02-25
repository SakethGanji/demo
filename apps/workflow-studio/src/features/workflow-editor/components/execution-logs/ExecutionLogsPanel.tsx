/**
 * Execution Logs Content
 *
 * Displays execution logs in a split view: node list + output preview.
 * Used inside the BottomPanel's Logs tab.
 */

import { useState, useEffect, useMemo } from 'react';
import {
  Trash2,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  ScrollText,
  Zap,
  Globe,
  GitBranch,
  ArrowDownToLine,
  ArrowUpFromLine,
  Hash,
  Timer,
  Repeat,
  Cpu,
  Route,
  HardDrive,
  Wrench,
  Bot,
  Copy,
  Check,
} from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { cn } from '@/shared/lib/utils';
import type { NodeMetrics } from '../../types/workflow';
import ExecutionTrace from './ExecutionTrace';
import { buildAgentTraceTree } from './buildTraceTree';

// ── Syntax-highlighted JSON viewer ──────────────────────────────────────
// Safe React-element based highlighting (no dangerouslySetInnerHTML)

function JsonView({ data, maxHeight = '12rem' }: { data: unknown; maxHeight?: string }) {
  const [copied, setCopied] = useState(false);
  const raw = JSON.stringify(data, null, 2);
  const lines = raw.split('\n');

  const handleCopy = () => {
    navigator.clipboard.writeText(raw);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="relative group">
      <button
        onClick={handleCopy}
        className="absolute top-1.5 right-1.5 p-1 rounded bg-muted-foreground/10 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
        title="Copy JSON"
      >
        {copied ? <Check size={11} /> : <Copy size={11} />}
      </button>
      <pre
        className="p-2.5 rounded-lg bg-[#0e0e0e] text-[11px] leading-[1.6] overflow-auto font-mono"
        style={{ maxHeight }}
      >
        {lines.map((line, i) => (
          <JsonLine key={i} line={line} />
        ))}
      </pre>
    </div>
  );
}

// Colorize a single JSON line
function JsonLine({ line }: { line: string }) {
  // Match leading whitespace
  const indentMatch = line.match(/^(\s*)/);
  const indent = indentMatch ? indentMatch[1] : '';
  const rest = line.slice(indent.length);

  // Key-value: "key": value
  const kvMatch = rest.match(/^"([^"]*)"(\s*:\s*)(.*)/);
  if (kvMatch) {
    const [, key, colon, val] = kvMatch;
    return (
      <div>
        {indent}
        <span style={{ color: '#9cdcfe' }}>"{key}"</span>
        <span style={{ color: '#d4d4d4' }}>{colon}</span>
        <JsonValue text={val} />
      </div>
    );
  }

  // Standalone value (array items, etc.)
  return <div>{indent}<JsonValue text={rest} /></div>;
}

// Colorize a JSON value portion
function JsonValue({ text }: { text: string }) {
  // String value: "..."  (possibly with trailing comma)
  const strMatch = text.match(/^"((?:[^"\\]|\\.)*)"(,?)$/);
  if (strMatch) {
    return (
      <>
        <span style={{ color: '#ce9178' }}>"{strMatch[1]}"</span>
        <span style={{ color: '#d4d4d4' }}>{strMatch[2]}</span>
      </>
    );
  }

  // Number
  const numMatch = text.match(/^(-?\d+\.?\d*(?:e[+-]?\d+)?)(,?)$/i);
  if (numMatch) {
    return (
      <>
        <span style={{ color: '#b5cea8' }}>{numMatch[1]}</span>
        <span style={{ color: '#d4d4d4' }}>{numMatch[2]}</span>
      </>
    );
  }

  // Boolean
  const boolMatch = text.match(/^(true|false)(,?)$/);
  if (boolMatch) {
    return (
      <>
        <span style={{ color: '#569cd6' }}>{boolMatch[1]}</span>
        <span style={{ color: '#d4d4d4' }}>{boolMatch[2]}</span>
      </>
    );
  }

  // Null
  const nullMatch = text.match(/^(null)(,?)$/);
  if (nullMatch) {
    return (
      <>
        <span style={{ color: '#569cd6' }}>{nullMatch[1]}</span>
        <span style={{ color: '#d4d4d4' }}>{nullMatch[2]}</span>
      </>
    );
  }

  // Brackets, braces, other
  return <span style={{ color: '#d4d4d4' }}>{text}</span>;
}

interface ExecutionLog {
  id: string;
  nodeName: string;
  nodeLabel: string;
  nodeType: string;
  status: 'idle' | 'running' | 'success' | 'error';
  timestamp: number;
  duration?: number;
  itemCount?: number;
  error?: string;
  metrics?: NodeMetrics;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(isoString: string): string {
  try {
    const d = new Date(isoString);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 });
  } catch {
    return isoString;
  }
}

// Metric entry with optional icon + color
interface MetricEntry {
  label: string;
  value: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  color?: string;
}

function getMetricEntries(metrics: NodeMetrics): MetricEntry[] {
  const entries: MetricEntry[] = [];

  // Timing
  if (metrics.startedAt) {
    entries.push({ label: 'Started', value: formatTime(metrics.startedAt), icon: Clock });
  }
  if (metrics.completedAt) {
    entries.push({ label: 'Completed', value: formatTime(metrics.completedAt), icon: Clock });
  }
  if (metrics.executionTimeMs != null) {
    entries.push({ label: 'Duration', value: formatDuration(metrics.executionTimeMs), icon: Timer });
  }
  if (metrics.executionOrder != null) {
    entries.push({ label: 'Exec Order', value: `#${metrics.executionOrder}`, icon: Hash });
  }

  // Data flow
  if (metrics.inputItemCount != null) {
    entries.push({ label: 'Input Items', value: String(metrics.inputItemCount), icon: ArrowDownToLine });
  }
  if (metrics.outputItemCount != null) {
    entries.push({ label: 'Output Items', value: String(metrics.outputItemCount), icon: ArrowUpFromLine });
  }
  if (metrics.inputDataSizeBytes != null) {
    entries.push({ label: 'Input Size', value: formatBytes(metrics.inputDataSizeBytes), icon: HardDrive });
  }
  if (metrics.outputDataSizeBytes != null) {
    entries.push({ label: 'Output Size', value: formatBytes(metrics.outputDataSizeBytes), icon: HardDrive });
  }

  // Retry
  if (metrics.retries != null && metrics.retries > 0) {
    entries.push({ label: 'Retries', value: `${metrics.retries} / ${metrics.maxRetries ?? 0}`, icon: Repeat });
  }

  // LLM
  if (metrics.model) {
    entries.push({ label: 'Model', value: metrics.model, icon: Cpu, color: 'text-purple-500' });
  }
  if (metrics.totalTokens != null) {
    entries.push({
      label: 'Tokens',
      value: `${(metrics.inputTokens ?? 0).toLocaleString()} in / ${(metrics.outputTokens ?? 0).toLocaleString()} out`,
      icon: Zap,
      color: 'text-purple-500',
    });
  }
  if (metrics.llmResponseTimeMs != null) {
    entries.push({ label: 'LLM Latency', value: formatDuration(metrics.llmResponseTimeMs), icon: Timer, color: 'text-purple-500' });
  }
  if (metrics.agentIterations != null) {
    entries.push({ label: 'Agent Loops', value: String(metrics.agentIterations), icon: Bot });
  }
  if (metrics.toolCallCount != null) {
    entries.push({ label: 'Tool Calls', value: String(metrics.toolCallCount), icon: Wrench });
  }

  // HTTP
  if (metrics.responseStatusCode != null) {
    const ok = metrics.responseStatusCode < 400;
    entries.push({ label: 'HTTP Status', value: String(metrics.responseStatusCode), icon: Globe, color: ok ? 'text-[var(--success)]' : 'text-destructive' });
  }
  if (metrics.requestMethod && metrics.requestUrl) {
    const url = metrics.requestUrl.length > 50 ? metrics.requestUrl.slice(0, 50) + '...' : metrics.requestUrl;
    entries.push({ label: 'Request', value: `${metrics.requestMethod} ${url}`, icon: Globe });
  }
  if (metrics.responseTimeMs != null) {
    entries.push({ label: 'Response Time', value: formatDuration(metrics.responseTimeMs), icon: Timer });
  }
  if (metrics.responseSizeBytes != null) {
    entries.push({ label: 'Response Size', value: formatBytes(metrics.responseSizeBytes), icon: HardDrive });
  }

  // Flow
  if (metrics.branchDecision != null) {
    entries.push({ label: 'Branch Taken', value: metrics.branchDecision, icon: GitBranch, color: 'text-blue-500' });
  }
  if (metrics.trueCount != null || metrics.falseCount != null) {
    entries.push({ label: 'True / False', value: `${metrics.trueCount ?? 0} / ${metrics.falseCount ?? 0}`, icon: Route });
  }
  if (metrics.activeOutputs && metrics.activeOutputs.length > 0) {
    entries.push({ label: 'Active Outputs', value: metrics.activeOutputs.join(', '), icon: Route });
  }

  return entries;
}

export default function ExecutionLogsPanel() {
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [dataTab, setDataTab] = useState<'output' | 'input'>('output');

  const executionData = useWorkflowStore((s) => s.executionData);
  const nodes = useWorkflowStore((s) => s.nodes);
  const clearExecutionData = useWorkflowStore((s) => s.clearExecutionData);

  // Convert execution data to logs format
  const logs: ExecutionLog[] = useMemo(() =>
    Object.entries(executionData)
      .map(([nodeId, data]): ExecutionLog | null => {
        const node = nodes.find((n) => n.id === nodeId);
        if (!node || node.type !== 'workflowNode') return null;

        return {
          id: nodeId,
          nodeName: node.data.name || nodeId,
          nodeLabel: node.data.label || node.data.name || 'Unknown',
          nodeType: node.data.type || '',
          status: data.status,
          timestamp: data.startTime || Date.now(),
          duration: data.metrics?.executionTimeMs,
          itemCount: data.metrics?.outputItemCount,
          error: data.output?.error,
          metrics: data.metrics,
        };
      })
      .filter((log): log is ExecutionLog => log !== null)
      .sort((a, b) => {
        const aOrder = a.metrics?.executionOrder ?? Infinity;
        const bOrder = b.metrics?.executionOrder ?? Infinity;
        if (aOrder !== bOrder) return (aOrder as number) - (bOrder as number);
        return a.timestamp - b.timestamp;
      }),
    [executionData, nodes]
  );

  const hasLogs = logs.length > 0;
  const isRunning = logs.some((l) => l.status === 'running');
  const hasErrors = logs.some((l) => l.status === 'error');
  const successCount = logs.filter((l) => l.status === 'success').length;
  const totalDuration = logs.reduce((sum, l) => sum + (l.duration || 0), 0);
  const totalTokens = logs.reduce((sum, l) => sum + ((l.metrics?.totalTokens as number) || 0), 0);

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

  // Reset data tab when switching nodes
  useEffect(() => {
    setDataTab('output');
  }, [selectedLogId]);

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
                  {formatDuration(totalDuration)}
                </span>
              )}
              {!isRunning && (
                <span className="text-[10px] text-muted-foreground">
                  {successCount}/{logs.length} nodes
                </span>
              )}
              {totalTokens > 0 && (
                <>
                  <div className="w-px h-3 bg-border" />
                  <span className="flex items-center gap-0.5 text-[10px] text-purple-500">
                    <Zap size={9} />
                    {totalTokens.toLocaleString()} tok
                  </span>
                </>
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
        <div className="w-48 border-r border-border overflow-y-auto bg-muted/30 shrink-0">
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
                  <div className="flex-1 min-w-0">
                    <span className="text-xs truncate block">{log.nodeLabel}</span>
                    <span className="text-[9px] text-muted-foreground truncate block">{log.nodeType}</span>
                  </div>
                  {log.duration !== undefined && (
                    <span className="text-[10px] text-muted-foreground flex-shrink-0">
                      {formatDuration(log.duration)}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Detail panel */}
        <div className="flex-1 overflow-auto p-3">
          {selectedLog && selectedNodeData ? (
            <div className="space-y-3">
              {/* Node header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium text-foreground">
                    {selectedLog.nodeLabel}
                  </h3>
                  <span className="text-[10px] text-muted-foreground font-mono">
                    {selectedLog.nodeType}
                  </span>
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
                      {formatDuration(selectedLog.duration)}
                    </span>
                  )}
                  {selectedLog.itemCount !== undefined && (
                    <span>{selectedLog.itemCount} item{selectedLog.itemCount !== 1 && 's'}</span>
                  )}
                  {selectedLog.metrics?.totalTokens != null && (
                    <span className="flex items-center gap-0.5 text-purple-500">
                      <Zap size={9} />
                      {(selectedLog.metrics.totalTokens as number).toLocaleString()}
                    </span>
                  )}
                  {selectedLog.metrics?.responseStatusCode != null && (
                    <span className={`flex items-center gap-0.5 ${
                      (selectedLog.metrics.responseStatusCode as number) < 400
                        ? 'text-[var(--success)]'
                        : 'text-destructive'
                    }`}>
                      <Globe size={9} />
                      {selectedLog.metrics.responseStatusCode}
                    </span>
                  )}
                  {selectedLog.metrics?.branchDecision != null && (
                    <span className="flex items-center gap-0.5 text-blue-500">
                      <GitBranch size={9} />
                      {selectedLog.metrics.branchDecision}
                    </span>
                  )}
                </div>
              </div>

              {/* Error */}
              {selectedLog.error && (
                <div className="p-2 rounded-lg bg-destructive/10 border border-destructive/20">
                  <p className="text-xs text-destructive">{selectedLog.error}</p>
                </div>
              )}

              {/* Agent execution trace */}
              {selectedNodeData.agentTrace && selectedNodeData.agentTrace.length > 0 && (
                <div className="space-y-1">
                  <h4 className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                    Execution Trace
                  </h4>
                  <div className="rounded-md border border-border bg-muted/20 overflow-auto" style={{ maxHeight: '24rem' }}>
                    <div className="p-2">
                      <ExecutionTrace
                        tree={buildAgentTraceTree(selectedNodeData.agentTrace, selectedLog.nodeName)}
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* Metrics detail grid */}
              {selectedLog.metrics && (() => {
                const entries = getMetricEntries(selectedLog.metrics!);
                if (entries.length === 0) return null;
                return (
                  <div className="space-y-1">
                    <h4 className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                      Metrics
                    </h4>
                    <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 p-2.5 rounded-lg bg-muted/50 border border-border">
                      {entries.map((entry) => {
                        const Icon = entry.icon;
                        return (
                          <div key={entry.label} className="flex items-center gap-1.5">
                            {Icon && <Icon size={10} className={entry.color || 'text-muted-foreground'} />}
                            <span className="text-[10px] text-muted-foreground whitespace-nowrap">{entry.label}</span>
                            <span className={`text-[11px] font-mono truncate ml-auto text-right ${entry.color || 'text-foreground'}`}>
                              {entry.value}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}

              {/* Input / Output toggle + data */}
              <div className="space-y-1">
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setDataTab('output')}
                    className={cn(
                      'text-[10px] font-medium uppercase tracking-wide px-2 py-0.5 rounded transition-colors',
                      dataTab === 'output'
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    Output {selectedNodeData.output?.items ? `(${selectedNodeData.output.items.length})` : ''}
                  </button>
                  <button
                    onClick={() => setDataTab('input')}
                    className={cn(
                      'text-[10px] font-medium uppercase tracking-wide px-2 py-0.5 rounded transition-colors',
                      dataTab === 'input'
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    Input {selectedNodeData.input?.items ? `(${selectedNodeData.input.items.length})` : ''}
                  </button>
                </div>

                {dataTab === 'output' && selectedNodeData.output?.items && selectedNodeData.output.items.length > 0 && (
                  <JsonView data={selectedNodeData.output.items} />
                )}
                {dataTab === 'output' && (!selectedNodeData.output?.items || selectedNodeData.output.items.length === 0) && !selectedLog.error && (
                  <p className="text-[11px] text-muted-foreground p-2">No output data</p>
                )}

                {dataTab === 'input' && selectedNodeData.input?.items && selectedNodeData.input.items.length > 0 && (
                  <JsonView data={selectedNodeData.input.items} />
                )}
                {dataTab === 'input' && (!selectedNodeData.input?.items || selectedNodeData.input.items.length === 0) && (
                  <p className="text-[11px] text-muted-foreground p-2">No input data</p>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <p className="text-xs text-muted-foreground">
                {hasLogs
                  ? 'Select a node to view details'
                  : 'Run workflow to see logs'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
