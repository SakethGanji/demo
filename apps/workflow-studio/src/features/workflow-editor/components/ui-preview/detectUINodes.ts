import type { Node } from 'reactflow';
import type { WorkflowNodeData } from '../../types/workflow';

type InputType = 'chat' | 'form' | null;
type OutputType = 'chat' | 'html' | 'markdown' | 'text';

export interface UIConfig {
  inputType: InputType;
  inputNode: Node<WorkflowNodeData> | null;
  outputNodes: Node<WorkflowNodeData>[];
  outputTypes: OutputType[];
  welcomeMessage?: string;
  placeholder?: string;
  autoResponse: boolean; // true when ChatInput should auto-display last node's output
}

// Node types use backend PascalCase format
const INPUT_NODE_TYPES: Record<string, InputType> = {
  ChatInput: 'chat',
  FormInput: 'form',
};

// Output nodes for explicit display control (ChatInput auto-displays, so no ChatOutput needed)
const OUTPUT_NODE_TYPES: Record<string, OutputType> = {
  HTMLDisplay: 'html',
  MarkdownDisplay: 'markdown',
  TextDisplay: 'text',
};

/**
 * Scans workflow nodes to detect UI nodes and build configuration
 * for the dynamic UI preview panel.
 */
export function detectUINodes(nodes: Node<WorkflowNodeData>[]): UIConfig {
  // Find input node (trigger)
  const inputNode = nodes.find((n) => INPUT_NODE_TYPES[n.data.type]) ?? null;
  const inputType = inputNode ? INPUT_NODE_TYPES[inputNode.data.type] : null;

  // Find explicit output nodes (HTMLDisplay, MarkdownDisplay, etc.)
  const outputNodes = nodes.filter((n) => OUTPUT_NODE_TYPES[n.data.type]);
  const outputTypes: OutputType[] = outputNodes.map((n) => OUTPUT_NODE_TYPES[n.data.type]);

  // ChatInput auto-displays last node's output (n8n-style)
  // Enable chat output when we have ChatInput
  const autoResponse = inputType === 'chat';
  if (autoResponse && !outputTypes.includes('chat')) {
    outputTypes.push('chat');
  }

  // Extract config from input node
  const welcomeMessage = inputNode?.data.parameters?.welcomeMessage as string | undefined;
  const placeholder = inputNode?.data.parameters?.placeholder as string | undefined;

  return {
    inputType,
    inputNode,
    outputNodes,
    outputTypes,
    welcomeMessage,
    placeholder,
    autoResponse,
  };
}

/**
 * Check if a workflow has UI nodes configured
 */
