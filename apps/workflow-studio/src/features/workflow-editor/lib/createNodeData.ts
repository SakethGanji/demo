/**
 * Shared factory for creating WorkflowNodeData and ReactFlow Node objects.
 *
 * All node creation paths (drag-drop, panel click, subworkflow embed, copy,
 * AI chat, backend load) should use these functions to ensure consistent
 * field population.
 */

import type { WorkflowNodeData, OutputStrategy } from '../types/workflow';
import type { NodeGroup, NodeIO } from './nodeStyles';
import { getNodeIcon, normalizeNodeGroup, isTriggerType } from './nodeConfig';
import { generateNodeName } from './workflowTransform';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Metadata input for the factory. Only `type` is required; everything else
 * has sensible defaults. Callers pass whatever they have available.
 */
export interface NodeTypeMetadata {
  type: string;

  // Display
  label?: string;
  displayName?: string;
  icon?: string;
  description?: string;

  // Categorisation
  group?: NodeGroup | string[];

  // I/O
  inputCount?: number;
  outputCount?: number;
  inputs?: NodeIO[];
  outputs?: NodeIO[];
  outputStrategy?: OutputStrategy;

  // Properties with defaults (for extracting default params)
  properties?: Array<{ name: string; default?: unknown; [k: string]: unknown }>;
}

/**
 * Per-instance overrides that differ between individual node instances
 * (e.g. name, position, parameters).
 */
interface NodeDataOverrides {
  name?: string;
  label?: string;
  parameters?: Record<string, unknown>;
  continueOnFail?: boolean;
  retryOnFail?: number;
  retryDelay?: number;
  pinnedData?: Array<{ json: Record<string, unknown> }>;
}

// ---------------------------------------------------------------------------
// Default I/O
// ---------------------------------------------------------------------------

/**
 * Returns default inputs and outputs for a given node type.
 * Centralises the If/Switch/Loop logic that was duplicated in
 * workflowTransform.ts and aiOperationApplier.ts.
 */
export function getDefaultIO(nodeType: string): {
  inputs: NodeIO[];
  outputs: NodeIO[];
} {
  const isTrigger = isTriggerType(nodeType);
  const inputs: NodeIO[] = isTrigger ? [] : [{ name: 'main', displayName: 'Main' }];

  let outputs: NodeIO[];
  switch (nodeType) {
    case 'If':
      outputs = [
        { name: 'true', displayName: 'True' },
        { name: 'false', displayName: 'False' },
      ];
      break;
    case 'Switch':
      outputs = [
        { name: 'output0', displayName: 'Output 0' },
        { name: 'output1', displayName: 'Output 1' },
        { name: 'fallback', displayName: 'Fallback' },
      ];
      break;
    case 'Loop':
      outputs = [
        { name: 'loop', displayName: 'Loop' },
        { name: 'done', displayName: 'Done' },
      ];
      break;
    case 'Poll':
      outputs = [
        { name: 'loop', displayName: 'Loop' },
        { name: 'done', displayName: 'Done' },
        { name: 'timeout', displayName: 'Timeout' },
      ];
      break;
    default:
      outputs = [{ name: 'main', displayName: 'Main' }];
  }

  return { inputs, outputs };
}

// ---------------------------------------------------------------------------
// Factory: WorkflowNodeData
// ---------------------------------------------------------------------------

/**
 * Builds a complete WorkflowNodeData from metadata + per-instance overrides.
 * Pure function, no hooks.
 *
 * @param meta     - Node type metadata (from API cache, drag data, etc.)
 * @param overrides - Per-instance values (name, parameters, etc.)
 * @param existingNames - Used to generate a unique name when `overrides.name`
 *                        is not provided. Mutated: pushes the generated name.
 */
export function createWorkflowNodeData(
  meta: NodeTypeMetadata,
  overrides?: NodeDataOverrides,
  existingNames?: string[],
): WorkflowNodeData {
  // Resolve name
  const name = overrides?.name
    ?? (existingNames
      ? generateNodeName(meta.type, existingNames)
      : meta.type);

  // Push into existingNames so subsequent calls in a loop stay unique
  if (existingNames && !overrides?.name) {
    existingNames.push(name);
  }

  // Resolve group
  const group: NodeGroup = Array.isArray(meta.group)
    ? normalizeNodeGroup(meta.group)
    : (meta.group as NodeGroup | undefined) ?? normalizeNodeGroup(undefined);

  // Resolve icon
  const icon = meta.icon
    ? getNodeIcon(meta.type, meta.icon)
    : getNodeIcon(meta.type);

  // Resolve I/O — prefer what meta provides, else use defaults
  const defaults = getDefaultIO(meta.type);
  const isTrigger = isTriggerType(meta.type);

  const inputs = meta.inputs ?? defaults.inputs;
  const outputs = meta.outputs ?? defaults.outputs;
  const inputCount = meta.inputCount ?? (isTrigger ? 0 : inputs.length);
  const outputCount = meta.outputCount ?? outputs.length;

  // Extract default parameters from properties
  const defaultParams: Record<string, unknown> = {};
  if (meta.properties) {
    for (const prop of meta.properties) {
      if (prop.default !== undefined) {
        defaultParams[prop.name] = prop.default;
      }
    }
  }

  const data: WorkflowNodeData = {
    name,
    type: meta.type,
    label: overrides?.label ?? meta.label ?? meta.displayName ?? name,
    icon,
    description: meta.description,
    parameters: overrides?.parameters ?? defaultParams,
    continueOnFail: overrides?.continueOnFail ?? false,
    retryOnFail: overrides?.retryOnFail ?? 0,
    retryDelay: overrides?.retryDelay ?? 1000,
    group,
    inputCount,
    outputCount,
    inputs,
    outputs,
    outputStrategy: meta.outputStrategy,
    ...(overrides?.pinnedData ? { pinnedData: overrides.pinnedData } : {}),
  };

  return data;
}

