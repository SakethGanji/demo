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
  AlertTriangle,
} from 'lucide-react';
import { Tabs, TabsList, TabsTab } from '@/shared/components/ui/tabs';
import { Input } from '@/shared/components/ui/input';
import { Textarea } from '@/shared/components/ui/textarea';
import { Switch } from '@/shared/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Field } from '@/shared/components/ui/field';
import { Spinner } from '@/shared/components/ui/spinner';
import { Tile, Callout } from '@/shared/components/ui/surface';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData, OnErrorMode } from '../../types/workflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import { isExtensionOwnedParam } from '../../lib/jsonParam';
import DynamicNodeForm, { type NodeProperty, type OutputSchema } from './DynamicNodeForm';
import { useNodeTypes } from '../../hooks/useNodeTypes';
import { getNodeExtensions } from './extensions/NodeExtensions';

const ON_ERROR_LABELS: Record<OnErrorMode, string> = {
  stop: 'Stop the workflow',
  continue: 'Continue the workflow',
  error_output: 'Send to the error output',
};

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
  const setNodeOnError = useWorkflowStore((s) => s.setNodeOnError);

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

  const nodeType = node.data.type;
  const formProperties = useMemo(
    () => ((nodeSchema?.properties ?? []) as NodeProperty[]).filter((p) => !isExtensionOwnedParam(nodeType, p.name, nodeParams)),
    [nodeSchema, nodeType, nodeParams],
  );

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
    <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'parameters' | 'settings')} className="h-full" data-testid="ndv-settings">
      <TabsList>
        <TabsTab value="parameters" data-testid="ndv-tab-parameters">Parameters</TabsTab>
        <TabsTab value="settings" data-testid="ndv-tab-settings">Settings</TabsTab>
      </TabsList>

      {/* Content */}
      <div className="flex-1 overflow-auto p-3">
        {activeTab === 'parameters' ? (
          <div className="space-y-3">
            {/* Node-specific extensions (cURL import, webhook URL, etc.) */}
            {getNodeExtensions(node.data.type).map((Extension, i) => (
              <Extension key={i} node={node} />
            ))}

            {/* Main Parameters Section */}
            <Tile className="overflow-hidden">
              <button
                onClick={() => toggleSection('main')}
                className="flex w-full items-center justify-between px-3 py-2 text-left row-hover transition-colors"
                data-testid="ndv-section-main"
              >
                <span className="text-panel-title text-foreground">
                  Main parameters
                </span>
                {expandedSections.main ? (
                  <ChevronUp size={14} className="text-muted-foreground" />
                ) : (
                  <ChevronDown size={14} className="text-muted-foreground" />
                )}
              </button>
              {expandedSections.main && (
                <div className="px-3 pt-1 pb-3">
                  {/* Dynamic form based on node schema from API */}
                  {isLoadingSchema ? (
                    <div className="flex items-center justify-center py-4">
                      <Spinner className="size-5 text-muted-foreground" />
                    </div>
                  ) : nodeSchema && nodeSchema.properties.length > 0 ? (
                    <DynamicNodeForm
                      properties={formProperties}
                      values={nodeParams}
                      onChange={handleParamChange}
                      allValues={nodeParams}
                      upstreamSchema={upstreamOutputSchema}
                      sampleData={upstreamSampleData}
                      allNodeData={allNodeData}
                    />
                  ) : nodeSchema && nodeSchema.properties.length === 0 ? (
                    <Callout tone="info" icon={<Info />}>
                      No configurable parameters.
                    </Callout>
                  ) : (
                    <Callout tone="error" icon={<AlertTriangle />}>
                      Unable to load schema.
                    </Callout>
                  )}
                </div>
              )}
            </Tile>

            {/* Options Section - Error Handling */}
            <Tile className="overflow-hidden">
              <button
                onClick={() => toggleSection('options')}
                className="flex w-full items-center justify-between px-3 py-2 text-left row-hover transition-colors"
                data-testid="ndv-section-errors"
              >
                <span className="text-panel-title text-foreground">Error handling</span>
                {expandedSections.options ? (
                  <ChevronUp size={14} className="text-muted-foreground" />
                ) : (
                  <ChevronDown size={14} className="text-muted-foreground" />
                )}
              </button>
              {expandedSections.options && (
                <div className="px-3 pt-1 pb-3">
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <span className="text-[13px] text-foreground">On error</span>
                      <Select
                        value={node.data.onError ?? 'stop'}
                        onValueChange={(v) => setNodeOnError(node.id, v as OnErrorMode)}
                        items={ON_ERROR_LABELS}
                      >
                        <SelectTrigger size="sm" className="w-full text-[12px]" data-testid="node-setting-on-error">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="stop" data-testid="on-error-stop">Stop the workflow</SelectItem>
                          <SelectItem value="continue" data-testid="on-error-continue">Continue the workflow</SelectItem>
                          <SelectItem value="error_output" data-testid="on-error-error-output">Send to the error output</SelectItem>
                        </SelectContent>
                      </Select>
                      <p className="text-[12px] text-muted-foreground">
                        What happens when this node fails after its retries.
                      </p>
                    </div>

                    <div className="space-y-2">
                      <label className="flex items-start gap-2.5 cursor-pointer">
                        <Switch
                          checked={(node.data.retryOnFail || 0) > 0}
                          onCheckedChange={(checked) => {
                            updateNodeData(node.id, {
                              retryOnFail: checked ? 3 : 0,
                            });
                          }}
                          className="mt-0.5"
                          data-testid="ndv-retry-on-fail"
                        />
                        <div>
                          <span className="text-[13px] text-foreground">Retry on fail</span>
                          <p className="text-[12px] text-muted-foreground">Retry if it fails</p>
                        </div>
                      </label>

                      {(node.data.retryOnFail || 0) > 0 && (
                        <div className="ml-10 flex flex-wrap gap-3">
                          <Field label="Retries">
                            <Input
                              type="number"
                              min={1}
                              max={10}
                              value={node.data.retryOnFail || 3}
                              onChange={(e) => {
                                const val = Math.min(10, Math.max(1, parseInt(e.target.value) || 1));
                                updateNodeData(node.id, { retryOnFail: val });
                              }}
                              className="w-16"
                              data-testid="ndv-retries"
                            />
                          </Field>
                          <Field label="Delay (ms)">
                            <Input
                              type="number"
                              min={0}
                              step={100}
                              value={node.data.retryDelay || 1000}
                              onChange={(e) => {
                                const val = Math.max(0, parseInt(e.target.value) || 1000);
                                updateNodeData(node.id, { retryDelay: val });
                              }}
                              className="w-24"
                              data-testid="ndv-retry-delay"
                            />
                          </Field>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </Tile>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Node Settings */}
            <Field label="Display name" help="Edit in the header above.">
              <Input
                type="text"
                value={node.data.name || node.data.label}
                disabled
                data-testid="ndv-display-name"
              />
            </Field>

            <Field label="Notes">
              <Textarea
                rows={2}
                placeholder="Add notes..."
                value={node.data.notes || ''}
                onChange={(e) => {
                  updateNodeData(node.id, { notes: e.target.value });
                }}
                data-testid="ndv-notes"
              />
            </Field>

            <label className="flex items-start gap-2.5 cursor-pointer">
              <Switch
                checked={node.data.disabled || false}
                onCheckedChange={(checked) => {
                  updateNodeData(node.id, { disabled: checked });
                }}
                className="mt-0.5"
                data-testid="ndv-disable-node"
              />
              <div>
                <span className="text-[13px] text-foreground">Disable node</span>
                <p className="text-xs text-muted-foreground">
                  Skipped during execution
                </p>
              </div>
            </label>
          </div>
        )}
      </div>
    </Tabs>
  );
}
