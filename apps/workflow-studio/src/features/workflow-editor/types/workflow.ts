import type { NodeGroup, NodeIO } from '../lib/nodeStyles';

// Subnode slot definition (from backend)
export interface SubnodeSlotDefinition {
  name: string;                        // "chatModel", "memory", "tools"
  displayName: string;                 // "Chat Model", "Memory", "Tool"
  slotType: 'model' | 'memory' | 'tool';
  required: boolean;
  multiple: boolean;                   // Can accept multiple subnodes (tools=true)
  acceptedNodeTypes?: string[];        // Restrict to specific node types
}

// Subnode type identifier
export type SubnodeType = 'model' | 'memory' | 'tool';

// Output strategy for dynamic output nodes (like Switch)
export interface OutputStrategy {
  type: 'dynamicFromCollection' | 'dynamicFromParameter' | 'static';
  collectionName?: string;  // For dynamicFromCollection: parameter name containing array
  parameter?: string;       // For dynamicFromParameter: parameter name with output count
  addFallback?: boolean;    // Add a fallback output
}

// Node data types - aligned with backend schema
export interface WorkflowNodeData {
  // Required fields for backend compatibility
  name: string;           // Unique identifier used in connections (maps to backend node.name)
  type: string;           // Node type (e.g., 'HttpRequest', 'If', 'Code')

  // Display fields
  label: string;          // Display name shown in UI (can differ from name)
  icon?: string;
  description?: string;

  // Node configuration
  parameters?: Record<string, unknown>;
  disabled?: boolean;

  // User notes
  notes?: string;

  // Error handling options
  continueOnFail?: boolean;
  retryOnFail?: number;   // 0-10
  retryDelay?: number;    // ms

  // Pinned data for testing (format: { json: {...} }[])
  pinnedData?: Array<{ json: Record<string, unknown> }>;

  // For sticky notes (UI-only)
  content?: string;
  color?: 'yellow' | 'blue' | 'green' | 'pink' | 'purple';

  // Dynamic node UI metadata
  group?: NodeGroup;                    // Node category for coloring
  inputCount?: number;                  // Number of input handles
  outputCount?: number;                 // Number of output handles
  inputs?: NodeIO[];                    // Input handle definitions with names
  outputs?: NodeIO[];                   // Output handle definitions with names
  outputStrategy?: OutputStrategy;      // How to calculate dynamic outputs

  // Subnode support
  isSubnode?: boolean;                  // True if this is a subnode type
  subnodeType?: SubnodeType;            // "model" | "memory" | "tool"
  providesToSlot?: string;              // Which slot this subnode provides to
  subnodeSlots?: SubnodeSlotDefinition[];  // Slots for subnodes (parent nodes only)
  nodeShape?: 'rectangular' | 'circular';  // Visual shape variant
  stacked?: boolean;                         // True when subnode is visually stacked inside parent badge

  // Subworkflow embedding
  subworkflowId?: string;  // workflow ID being embedded (for ExecuteWorkflow nodes)
}

export interface StickyNoteData {
  content: string;
  color: 'yellow' | 'blue' | 'green' | 'pink' | 'purple';
  width?: number;
  height?: number;
}

// Node definition for the node creator panel
export interface NodeDefinition {
  type: string;
  name: string;
  displayName: string;
  description: string;
  icon: string;
  category: 'trigger' | 'action' | 'transform' | 'flow' | 'helper' | 'ai';
  subcategory?: string;
}

// Node creator view types
export type NodeCreatorView = 'trigger' | 'regular' | 'ai' | 'subnode';

// Subnode slot context for node creator
export interface SubnodeSlotContext {
  parentNodeId: string;
  slotName: string;
  slotType: SubnodeType;
}

// Execution data
interface ExecutionData {
  items: Record<string, unknown>[];
  error?: string;
}

export interface NodeExecutionData {
  input: ExecutionData | null;
  output: ExecutionData | null;
  startTime?: number;
  endTime?: number;
  status: 'idle' | 'running' | 'success' | 'error';
}

// Subnode edge data
export interface SubnodeEdgeData {
  isSubnodeEdge: true;
  slotName: string;           // "chatModel", "memory", "tools"
  slotType: SubnodeType;      // "model", "memory", "tool"
}

