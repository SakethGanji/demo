import { useState, useMemo, useCallback } from 'react';
import {
  useUpstreamNodeId,
  useNodeById,
  useUpstreamSampleData,
  useAllNodeExecData,
  useConnectedSubnodes,
} from '../../hooks/useWorkflowSelectors';
import {
  Info,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Loader2,
  Plus,
  Bot,
  Database,
  Wrench,
  X,
  AlertTriangle,
} from 'lucide-react';
import type { Node } from 'reactflow';
import type { WorkflowNodeData, SubnodeSlotDefinition, SubnodeType } from '../../types/workflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import DynamicNodeForm, { type NodeProperty, type OutputSchema } from './DynamicNodeForm';
import { useNodeTypes } from '../../hooks/useNodeTypes';
import { cn } from '@/shared/lib/utils';
import { getNodeExtensions } from './extensions/NodeExtensions';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/shared/components/ui/alert-dialog';

interface NodeSettingsProps {
  node: Node<WorkflowNodeData>;
}

// Icon mapping for subnode types
const subnodeTypeIcons: Record<SubnodeType, typeof Bot> = {
  model: Bot,
  memory: Database,
  tool: Wrench,
};

// Accent colors for subnode types — uses CSS custom properties from theme
const subnodeTypeColors: Record<SubnodeType, string> = {
  model: 'var(--subnode-model)',
  memory: 'var(--subnode-memory)',
  tool: 'var(--subnode-tool)',
};

export default function NodeSettings({ node }: NodeSettingsProps) {
  const [activeTab, setActiveTab] = useState<'parameters' | 'settings'>('parameters');
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    main: true,
    subnodes: true,
    options: false,
  });
  const [expandedSubnodes, setExpandedSubnodes] = useState<Record<string, boolean>>({});
  // Inline subnode picker state
  const [subnodePickerSlot, setSubnodePickerSlot] = useState<{
    parentId: string;
    slotName: string;
    slotType: SubnodeType;
    displayName: string;
  } | null>(null);

  // Individual selectors — actions are stable refs, data only triggers re-render when it changes
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);
  const deleteNode = useWorkflowStore((s) => s.deleteNode);

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
  const connectedSubnodes = useConnectedSubnodes(
    node.id,
    node.data.subnodeSlots as SubnodeSlotDefinition[] | undefined
  );

  // Compatible subnodes for the picker dialog
  const compatibleSubnodes = useMemo(() => {
    if (!subnodePickerSlot || !nodeTypes) return [];
    return nodeTypes.filter(
      (n) => (n as Record<string, unknown>).subnodeType === subnodePickerSlot.slotType
    );
  }, [subnodePickerSlot, nodeTypes]);

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

            {/* Connected Subnodes Section - only show if node has subnode slots */}
            {connectedSubnodes && (
              <div className="rounded border border-border/40">
                <button
                  onClick={() => toggleSection('subnodes')}
                  className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-accent/50 transition-colors"
                >
                  <span className="text-[12px] font-medium text-foreground/80">
                    Connected Subnodes
                  </span>
                  {expandedSections.subnodes ? (
                    <ChevronUp size={14} className="text-muted-foreground" />
                  ) : (
                    <ChevronDown size={14} className="text-muted-foreground" />
                  )}
                </button>
                {expandedSections.subnodes && (
                  <div className="border-t border-border/30 px-3 py-2.5 space-y-3">
                    {connectedSubnodes.slots.map((slot) => {
                      const slotSubnodes = connectedSubnodes.slotMap[slot.name] || [];
                      const SlotIcon = subnodeTypeIcons[slot.slotType as SubnodeType] || Wrench;
                      const slotColor = subnodeTypeColors[slot.slotType as SubnodeType] || 'var(--muted-foreground)';
                      const canAddMore = slot.multiple || slotSubnodes.length === 0;

                      return (
                        <div key={slot.name} className="space-y-2">
                          {/* Slot header */}
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <SlotIcon size={14} style={{ color: slotColor }} />
                              <span className="text-xs font-medium text-foreground">
                                {slot.displayName}
                                {slot.required && <span className="text-destructive ml-0.5">*</span>}
                              </span>
                            </div>
                            {canAddMore && (
                              <button
                                onClick={() => {
                                  setSubnodePickerSlot({
                                    parentId: node.id,
                                    slotName: slot.name,
                                    slotType: slot.slotType as SubnodeType,
                                    displayName: slot.displayName,
                                  });
                                }}
                                className="flex items-center gap-1 px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent rounded transition-colors"
                              >
                                <Plus size={12} />
                                Add
                              </button>
                            )}
                          </div>

                          {/* Connected subnodes list */}
                          {slotSubnodes.length > 0 ? (
                            <div className="space-y-1.5 ml-5">
                              {slotSubnodes.map((subnode) => {
                                const isExpanded = expandedSubnodes[subnode.id] || false;
                                return (
                                  <div key={subnode.id} className="space-y-0">
                                    <div
                                      className="flex items-center justify-between rounded-md border border-border/50 bg-muted/30 px-2.5 py-1.5 group"
                                    >
                                      <button
                                        onClick={() => setExpandedSubnodes(prev => ({ ...prev, [subnode.id]: !prev[subnode.id] }))}
                                        className="flex items-center gap-2 text-xs text-foreground hover:text-primary transition-colors"
                                      >
                                        {isExpanded ? (
                                          <ChevronDown size={12} className="text-muted-foreground" />
                                        ) : (
                                          <ChevronRight size={12} className="text-muted-foreground" />
                                        )}
                                        <div
                                          className="w-5 h-5 rounded-full flex items-center justify-center"
                                          style={{ backgroundColor: `${slotColor}20`, color: slotColor }}
                                        >
                                          <SlotIcon size={10} />
                                        </div>
                                        <span>{subnode.data.label}</span>
                                      </button>
                                      <button
                                        onClick={() => deleteNode(subnode.id)}
                                        className="p-1 text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-all"
                                        title="Remove subnode"
                                      >
                                        <X size={12} />
                                      </button>
                                    </div>
                                    {isExpanded && (
                                      <SubnodeInlineForm subnode={subnode} nodeTypes={nodeTypes} />
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="ml-5 text-xs text-muted-foreground/60 italic">
                              No {slot.displayName.toLowerCase()} connected
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

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
                              className="w-16 rounded-md border border-input bg-background px-2 py-1 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
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
                              className="w-24 rounded-md border border-input bg-background px-2 py-1 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
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
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
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

      {/* Inline Subnode Picker Dialog */}
      <AlertDialog open={!!subnodePickerSlot} onOpenChange={(open) => { if (!open) setSubnodePickerSlot(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Add {subnodePickerSlot?.displayName}
            </AlertDialogTitle>
            <AlertDialogDescription>
              Choose a {subnodePickerSlot?.slotType} to connect.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="mt-3 space-y-1.5 max-h-64 overflow-auto">
            {compatibleSubnodes.map((subnodeDef) => {
              const defAny = subnodeDef as Record<string, unknown>;
              const SlotIcon = subnodeTypeIcons[subnodePickerSlot?.slotType as SubnodeType] || Wrench;
              const slotColor = subnodeTypeColors[subnodePickerSlot?.slotType as SubnodeType] || 'var(--muted-foreground)';
              return (
                <button
                  key={subnodeDef.type}
                  onClick={() => {
                    if (!subnodePickerSlot) return;
                    const defaultParams: Record<string, unknown> = {};
                    if (subnodeDef.properties) {
                      for (const prop of subnodeDef.properties) {
                        if ((prop as Record<string, unknown>).default !== undefined) {
                          defaultParams[(prop as Record<string, unknown>).name as string] = (prop as Record<string, unknown>).default;
                        }
                      }
                    }
                    useWorkflowStore.getState().addSubnode(
                      subnodePickerSlot.parentId,
                      subnodePickerSlot.slotName,
                      {
                        type: subnodeDef.type,
                        label: (defAny.displayName as string) || subnodeDef.type,
                        icon: defAny.icon as string | undefined,
                        subnodeType: subnodePickerSlot.slotType,
                        properties: defaultParams,
                      }
                    );
                    setSubnodePickerSlot(null);
                  }}
                  className="w-full flex items-center gap-3 rounded-md border border-border px-3 py-2.5 text-left hover:bg-accent transition-colors"
                >
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
                    style={{ backgroundColor: `${slotColor}20`, color: slotColor }}
                  >
                    <SlotIcon size={14} />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">
                      {(defAny.displayName as string) || subnodeDef.type}
                    </div>
                    {defAny.description && (
                      <div className="text-xs text-muted-foreground truncate">
                        {defAny.description as string}
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
            {compatibleSubnodes.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-4">
                No compatible {subnodePickerSlot?.slotType} types available.
              </p>
            )}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// --- Inline subnode configuration form ---

interface SubnodeInlineFormProps {
  subnode: Node<WorkflowNodeData>;
  nodeTypes: { type: string; properties: { name: string; displayName: string; type: string; default?: unknown; required?: boolean; description?: string; options?: { name: string; value: unknown }[] }[] }[] | undefined;
}

function SubnodeInlineForm({ subnode, nodeTypes }: SubnodeInlineFormProps) {
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);
  const subnodeSchema = nodeTypes?.find((n) => n.type === subnode.data.type);

  if (!subnodeSchema?.properties?.length) {
    return (
      <div className="ml-3 mt-1 rounded-md border border-border/40 bg-muted/20 px-3 py-2">
        <p className="text-xs text-muted-foreground">No configurable parameters.</p>
      </div>
    );
  }

  return (
    <div className="ml-3 mt-1 rounded-md border border-border/40 bg-muted/20 px-3 py-3">
      <DynamicNodeForm
        properties={subnodeSchema.properties as NodeProperty[]}
        values={(subnode.data.parameters as Record<string, unknown>) || {}}
        onChange={(key, value) => {
          updateNodeData(subnode.id, {
            parameters: { ...subnode.data.parameters, [key]: value },
          });
        }}
        allValues={(subnode.data.parameters as Record<string, unknown>) || {}}
      />
    </div>
  );
}
