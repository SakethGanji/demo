import { useState, useMemo, useCallback } from 'react';
import {
  useUpstreamNodeId,
  useNodeById,
  useUpstreamSampleData,
  useAllNodeExecData,
} from '../../hooks/useWorkflowSelectors';
import {
  Info,
  ChevronDown,
  ChevronUp,
  Loader2,
  AlertTriangle,
} from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData } from '../../types/workflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import DynamicNodeForm, { type NodeProperty, type OutputSchema } from './DynamicNodeForm';
import { useNodeTypes } from '../../hooks/useNodeTypes';
import { cn } from '@/shared/lib/utils';
import { getNodeExtensions } from './extensions/NodeExtensions';

interface NodeSettingsProps {
  node: Node<WorkflowNodeData>;
}

export default function NodeSettings({ node }: NodeSettingsProps) {
  const [activeTab, setActiveTab] = useState<'parameters' | 'settings'>('parameters');
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    main: true,
    options: false,
  });

  // Individual selectors — actions are stable refs, data only triggers re-render when it changes
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);

  const upstreamNodeId = useUpstreamNodeId(node.id);
  const upstreamNode = useNodeById(upstreamNodeId);

  // Stable params reference and onChange callback — prevents DynamicNodeForm re-renders
  const nodeParams = useMemo(
    () => (node.data.parameters as Record<string, unknown>) || {},
    [node.data.parameters]
  );
  const handleParamChange = useCallback((key: string, value: unknown) => {
    const store = useWorkflowStore.getState();
    const currentNode = store.nodes.find((n) => n.id === node.id);
    if (!currentNode) return;
    store.updateNodeData(node.id, {
      parameters: { ...(currentNode.data as WorkflowNodeData).parameters, [key]: value },
    });
  }, [node.id]);

  // Fetch node type schema from API
  const { data: nodeTypes, isLoading: isLoadingSchema } = useNodeTypes();

  // Get the schema for this node type (type is already backend format)
  const nodeSchema = nodeTypes?.find((n) => n.type === node.data.type);

  const upstreamNodeSchema = upstreamNode?.data?.type
    ? nodeTypes?.find((n) => n.type === upstreamNode.data.type)
    : null;

  // Get the output schema from the upstream node's first output
  const upstreamOutputSchema = upstreamNodeSchema?.outputs?.[0]?.schema as OutputSchema | undefined;

  const upstreamSampleData = useUpstreamSampleData(upstreamNodeId);
  const allNodeData = useAllNodeExecData();

  const toggleSection = (section: string) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  return (
    <div className="flex h-full flex-col">
      {/* Compact Tabs */}
      <div className="flex border-b border-border/50 px-1">
        <button
          onClick={() => setActiveTab('parameters')}
          className={cn(
            'relative flex-1 px-3 py-1.5 text-[12px] font-medium transition-colors',
            activeTab === 'parameters'
              ? 'text-foreground after:absolute after:bottom-0 after:left-1 after:right-1 after:h-0.5 after:bg-primary after:rounded-full'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          Parameters
        </button>
        <button
          onClick={() => setActiveTab('settings')}
          className={cn(
            'relative flex-1 px-3 py-1.5 text-[12px] font-medium transition-colors',
            activeTab === 'settings'
              ? 'text-foreground after:absolute after:bottom-0 after:left-1 after:right-1 after:h-0.5 after:bg-primary after:rounded-full'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          Settings
        </button>
      </div>

      {/* Content - tighter padding */}
      <div className="flex-1 overflow-auto p-3">
        {activeTab === 'parameters' ? (
          <div className="space-y-3">
            {/* Node-specific extensions (cURL import, webhook URL, etc.) */}
            {getNodeExtensions(node.data.type).map((Extension, i) => (
              <Extension key={i} node={node} />
            ))}

            {/* Main Parameters Section */}
            <div className="rounded border border-border/40">
              <button
                onClick={() => toggleSection('main')}
                className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-accent/50 transition-colors"
              >
                <span className="text-[12px] font-medium text-foreground/80">
                  Main Parameters
                </span>
                {expandedSections.main ? (
                  <ChevronUp size={14} className="text-muted-foreground" />
                ) : (
                  <ChevronDown size={14} className="text-muted-foreground" />
                )}
              </button>
              {expandedSections.main && (
                <div className="border-t border-border/30 px-3 py-2.5">
                  {/* Dynamic form based on node schema from API */}
                  {isLoadingSchema ? (
                    <div className="flex items-center justify-center py-4">
                      <Loader2 size={20} className="animate-spin text-muted-foreground" />
                    </div>
                  ) : nodeSchema && nodeSchema.properties.length > 0 ? (
                    <DynamicNodeForm
                      properties={nodeSchema.properties as NodeProperty[]}
                      values={nodeParams}
                      onChange={handleParamChange}
                      allValues={nodeParams}
                      upstreamSchema={upstreamOutputSchema}
                      sampleData={upstreamSampleData}
                      allNodeData={allNodeData}
                    />
                  ) : nodeSchema && nodeSchema.properties.length === 0 ? (
                    <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                      <Info size={14} />
                      <span>No configurable parameters.</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                      <AlertTriangle size={14} />
                      <span>Unable to load schema.</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Options Section - Error Handling */}
            <div className="rounded border border-border/40">
              <button
                onClick={() => toggleSection('options')}
                className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-accent/50 transition-colors"
              >
                <span className="text-[12px] font-medium text-foreground/80">Error Handling</span>
                {expandedSections.options ? (
                  <ChevronUp size={14} className="text-muted-foreground" />
                ) : (
                  <ChevronDown size={14} className="text-muted-foreground" />
                )}
              </button>
              {expandedSections.options && (
                <div className="border-t border-border/30 px-3 py-2.5">
                  <div className="space-y-3">
                    <label className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={node.data.continueOnFail || false}
                        onChange={(e) => {
                          updateNodeData(node.id, {
                            continueOnFail: e.target.checked,
                          });
                        }}
                        className="mt-0.5 h-3.5 w-3.5 rounded border-input text-primary focus:ring-ring accent-primary"
                      />
                      <div>
                        <span className="text-[13px] text-foreground">Continue on fail</span>
                        <p className="text-[12px] text-muted-foreground">
                          Continue even if this node fails
                        </p>
                      </div>
                    </label>

                    <div className="space-y-2">
                      <label className="flex items-start gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={(node.data.retryOnFail || 0) > 0}
                          onChange={(e) => {
                            updateNodeData(node.id, {
                              retryOnFail: e.target.checked ? 3 : 0,
                            });
                          }}
                          className="mt-0.5 h-3.5 w-3.5 rounded border-input text-primary focus:ring-ring accent-primary"
                        />
                        <div>
                          <span className="text-[13px] text-foreground">Retry on fail</span>
                          <p className="text-[12px] text-muted-foreground">Retry if it fails</p>
                        </div>
                      </label>

                      {(node.data.retryOnFail || 0) > 0 && (
                        <div className="ml-6 flex flex-wrap gap-3">
                          <div>
                            <label className="mb-1 block text-xs text-muted-foreground">
                              Retries
                            </label>
                            <input
                              type="number"
                              min={1}
                              max={10}
                              value={node.data.retryOnFail || 3}
                              onChange={(e) => {
                                const val = Math.min(10, Math.max(1, parseInt(e.target.value) || 1));
                                updateNodeData(node.id, { retryOnFail: val });
                              }}
                              className="w-16 rounded-md border border-input bg-[var(--surface)] px-2 py-1 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
                            />
                          </div>
                          <div>
                            <label className="mb-1 block text-xs text-muted-foreground">
                              Delay (ms)
                            </label>
                            <input
                              type="number"
                              min={0}
                              step={100}
                              value={node.data.retryDelay || 1000}
                              onChange={(e) => {
                                const val = Math.max(0, parseInt(e.target.value) || 1000);
                                updateNodeData(node.id, { retryDelay: val });
                              }}
                              className="w-24 rounded-md border border-input bg-[var(--surface)] px-2 py-1 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Node Settings */}
            <div>
              <label className="mb-1 block text-xs font-medium text-foreground">
                Display Name
              </label>
              <input
                type="text"
                value={node.data.name || node.data.label}
                disabled
                className="w-full rounded-md border border-input bg-muted px-2 py-1.5 text-sm text-muted-foreground cursor-not-allowed"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Edit in the header above.
              </p>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-foreground">
                Notes
              </label>
              <textarea
                rows={2}
                placeholder="Add notes..."
                value={node.data.notes || ''}
                onChange={(e) => {
                  updateNodeData(node.id, { notes: e.target.value });
                }}
                className="w-full rounded-md border border-input bg-[var(--surface)] px-2 py-1.5 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>

            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={node.data.disabled || false}
                onChange={(e) => {
                  updateNodeData(node.id, { disabled: e.target.checked });
                }}
                className="mt-0.5 h-4 w-4 rounded border-input text-primary focus:ring-ring"
              />
              <div>
                <span className="text-sm text-foreground">Disable node</span>
                <p className="text-xs text-muted-foreground">
                  Skipped during execution
                </p>
              </div>
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
