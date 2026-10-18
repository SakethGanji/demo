import { memo, useState, useMemo, useCallback } from 'react';
import { DataEmpty } from './DataEmpty';
import { Database, Code, Pin, Zap, Globe, GitBranch, Download, History, FileCheck2 } from 'lucide-react';
import { toast } from 'sonner';
import type { NodeExecutionData } from '../../types/workflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNDVStore } from '../../stores/ndvStore';
import { Segmented } from '@/shared/components/ui/segmented';
import { Tip } from '@/shared/components/ui/tooltip';
import { Spinner } from '@/shared/components/ui/spinner';
import { Callout } from '@/shared/components/ui/surface';
import { pinFromLastRun } from '../../lib/nodeActions';
import { Button } from '@/shared/components/ui/button';
import { useNodeActions } from '../../hooks/useNodeActions';
import type { NodeContractOverride } from '../../lib/shapes';
import { isObservedType } from '../../lib/contractSuggestions';
import { learnContract, removeContract, type ContractProposal } from '../../lib/contractActions';
import RunDataDisplay from './RunDataDisplay';
import ContractDialog from './ContractDialog';

interface OutputPanelProps {
  nodeId: string;
  executionData: NodeExecutionData | null;
}

type DisplayMode = 'json' | 'schema';

const OutputPanel = memo(function OutputPanel({ nodeId, executionData }: OutputPanelProps) {
  const storedOutputMode = useNDVStore((s) => s.outputDisplayMode);
  const [displayMode, setDisplayMode] = useState<DisplayMode>(storedOutputMode);

  const hasPinned = useWorkflowStore((s) => s.hasPinnedData(nodeId));
  const getPinnedDataForDisplay = useWorkflowStore((s) => s.getPinnedDataForDisplay);
  const pinNodeData = useWorkflowStore((s) => s.pinNodeData);
  const unpinNodeData = useWorkflowStore((s) => s.unpinNodeData);

  const isPinned = hasPinned;

  // Learned contracts (observed-type nodes only)
  const nodeType = useWorkflowStore((s) => s.nodes.find((n) => n.id === nodeId)?.data?.type) as string | undefined;
  const contract = useWorkflowStore((s) => s.nodes.find((n) => n.id === nodeId)?.data?.contract) as NodeContractOverride | undefined;
  const { saveNew } = useNodeActions();
  const [proposal, setProposal] = useState<ContractProposal | null>(null);
  const [contractBusy, setContractBusy] = useState(false);

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

  const handleLearn = async () => {
    setContractBusy(true);
    const res = await learnContract(nodeId, saveNew);
    setContractBusy(false);
    if (res.ok) setProposal(res.proposal);
    else toast.error('Could not learn a contract', { description: res.message });
  };

  const handleRemove = async () => {
    setContractBusy(true);
    const res = await removeContract(nodeId, saveNew);
    setContractBusy(false);
    if (res.ok) toast.success('Contract removed');
    else toast.error('Could not remove the contract', { description: res.message });
  };
  const showLearn = isObservedType(nodeType) && !!hasData && !contract;
  const hasError = executionData?.output?.error;
  const itemCount = executionData?.output?.items?.length ?? 0;
  const metrics = executionData?.metrics;

  // Detect PDF base64 in output
  const pdfBase64 = useMemo(() => {
    const items = displayData;
    if (!items || items.length === 0) return null;
    const first = items[0] as Record<string, unknown>;
    if (first && typeof first.pdf_base64 === 'string' && first.pdf_base64.length > 0) {
      return first.pdf_base64;
    }
    return null;
  }, [displayData]);

  const handleDownloadPdf = useCallback(() => {
    if (!pdfBase64) return;
    const byteChars = atob(pdfBase64);
    const byteNumbers = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
    const blob = new Blob([byteNumbers], { type: 'application/pdf' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'report.pdf';
    a.click();
    URL.revokeObjectURL(url);
  }, [pdfBase64]);

  // Use server-accurate execution time
  const executionTime = metrics?.executionTimeMs != null
    ? metrics.executionTimeMs < 1000
      ? `${Math.round(metrics.executionTimeMs)}ms`
      : `${(metrics.executionTimeMs / 1000).toFixed(2)}s`
    : null;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 shadow-[inset_0_-1px_0_var(--hairline)]">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden whitespace-nowrap [&>*]:shrink-0">
          <span className="text-[12.5px] font-medium text-foreground">Output</span>
          {hasData && (
            <span className="rounded-chip px-1 py-px text-[10.5px] font-medium bg-success/11 text-success-text">
              {itemCount} {itemCount === 1 ? 'item' : 'items'}
            </span>
          )}
          {hasError && (
            <span className="rounded-chip px-1 py-px text-[10.5px] font-medium bg-destructive-soft text-destructive">
              Error
            </span>
          )}
          {executionTime && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground" title={`Execution time ${executionTime}`}>
              {executionTime}
            </span>
          )}
          {/* LLM token badge */}
          {metrics?.totalTokens != null && (
            <span className="flex items-center gap-1 rounded-chip px-1 py-px text-[10.5px] font-medium bg-ai-soft text-ai-text" title={`In: ${metrics.inputTokens ?? 0} / Out: ${metrics.outputTokens ?? 0}`}>
              <Zap size={10} />
              {metrics.totalTokens.toLocaleString()} tok
            </span>
          )}
          {/* HTTP status badge */}
          {metrics?.responseStatusCode != null && (
            <span title={`HTTP status ${metrics.responseStatusCode}`} className={`flex items-center gap-1 rounded-sm px-1 py-px text-[10px] font-medium ${
              metrics.responseStatusCode < 400
                ? 'bg-success/11 text-success-text'
                : 'bg-destructive-soft text-destructive'
            }`}>
              <Globe size={10} />
              {metrics.responseStatusCode}
            </span>
          )}
          {/* Branch decision badge */}
          {metrics?.branchDecision != null && (
            <span className="flex items-center gap-1 rounded-chip px-1 py-px text-[10.5px] font-medium bg-brand-soft text-primary">
              <GitBranch size={10} />
              {metrics.branchDecision}
            </span>
          )}
          {isPinned && (
            <span className="rounded-chip px-1 py-px text-[10.5px] font-medium bg-warning/13 text-warning-text">
              Pinned
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-0.5">
          {/* Pin button */}
          <button
            onClick={handlePinToggle}
            disabled={!hasData && !isPinned}
            className={`rounded-ctl p-1.5 transition-colors ${
              isPinned
                ? 'bg-warning/13 text-warning-text'
                : 'text-muted-foreground hover:bg-(--hover-fill) hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed'
            }`}
            title={isPinned ? 'Unpin data' : 'Pin data'}
            aria-label={isPinned ? 'Unpin data' : 'Pin data'}
            data-testid="ndv-output-pin"
          >
            <Pin size={13} />
          </button>

          {/* Pin this node's output from the run data on the canvas (replaces an older pin) */}
          <Tip label="Pin this node's output from the last run">
            <button
              onClick={() => pinFromLastRun(nodeId)}
              disabled={!hasData}
              className="rounded-ctl p-1.5 text-muted-foreground transition-colors hover:bg-(--hover-fill) hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Pin from last run"
              data-testid="ndv-pin-from-last-run"
            >
              <History size={13} />
            </button>
          </Tip>

          {/* PDF download button */}
          {pdfBase64 && (
            <button
              onClick={handleDownloadPdf}
              className="rounded-ctl p-1.5 text-muted-foreground hover:bg-(--hover-fill) hover:text-foreground transition-colors"
              title="Download PDF"
              aria-label="Download PDF"
              data-testid="ndv-output-pdf"
            >
              <Download size={13} />
            </button>
          )}

          {/* Display mode toggle */}
          <Segmented
          size="xs"
          value={displayMode}
          onChange={(m) => { setDisplayMode(m); useNDVStore.getState().setOutputDisplayMode(m); }}
          aria-label="Display mode"
          data-testid="ndv-output-display-mode"
          options={[
            { key: 'schema', label: <Database size={13} aria-label="Schema view" />, testId: 'ndv-output-mode-schema' },
            { key: 'json', label: <Code size={13} aria-label="JSON view" />, testId: 'ndv-output-mode-json' },
          ]}
        />
        </div>
      </div>

      {/* Learned contract */}
      {(showLearn || contract) && (
        <div className="flex items-center justify-between gap-2 px-3 py-1.5 shadow-[inset_0_-1px_0_var(--hairline)]">
          <span
            className="min-w-0 truncate text-[11px] text-muted-foreground"
            title={contract ? undefined : 'Output shape is unknown until you approve a contract'}
          >
            {contract
              ? `Contract approved by ${contract.approved_by}`
              : 'Shape not approved yet'}
          </span>
          {contract ? (
            <Button size="xs" variant="ghost" onClick={handleRemove} disabled={contractBusy} data-testid="ndv-contract-remove">
              Remove contract
            </Button>
          ) : (
            <Button size="xs" variant="outline" onClick={handleLearn} disabled={contractBusy} data-testid="ndv-contract-learn">
              <FileCheck2 />
              Use as contract
            </Button>
          )}
        </div>
      )}
      <ContractDialog proposal={proposal} onOpenChange={(o) => { if (!o) setProposal(null); }} />

      {/* Data display */}
      <div className="well m-2 flex-1 overflow-auto p-3">
        {!executionData ? (
          <DataEmpty title="No output data yet" hint="Run the workflow to see results" />
        ) : executionData.status === 'running' ? (
          <div className="flex h-full flex-col items-center justify-center">
            <Spinner className="size-6" />
            <p className="mt-2 text-[13px] font-medium text-foreground">Executing...</p>
          </div>
        ) : hasError ? (
          <Callout tone="error" className="items-start">
            <p className="text-[13px] font-semibold">Error</p>
            <p className="mt-1 text-[12px]">
              {executionData.output?.error}
            </p>
          </Callout>
        ) : displayData && displayData.length > 0 ? (
          <RunDataDisplay
            data={displayData}
            mode={displayMode}
          />
        ) : (
          <DataEmpty title="No output items" hint="The node executed but returned no data" />
        )}
      </div>

    </div>
  );
});

export default OutputPanel;
