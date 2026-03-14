/**
 * Node styling utilities for dynamic group-based colors and sizing
 *
 * Note: Node groups should come from the backend API. The normalizeNodeGroup
 * function in nodeConfig.ts handles normalization. This file only contains
 * styling-related utilities.
 */

import type { NodeGroup } from './nodeConfig';

// Re-export NodeGroup type for convenience
export type { NodeGroup };

interface NodeStyleConfig {
  group: NodeGroup;
  bgColor: string;
  borderColor: string;
  iconBgColor: string;
  iconFgColor: string;
  accentColor: string;
  handleColor: string;
}

interface NodeShapeConfig {
  borderRadius: string; // CSS border-radius value (can be asymmetric)
  accentType: 'left-bar' | 'bottom-bar' | 'diamond' | 'shimmer' | 'none';
}

interface NodeDimensions {
  width: number;
  height: number;
  minWidth: number;
}

export interface NodeIO {
  name: string;
  displayName?: string;
}

/**
 * Get CSS variable-based styles for a node group
 */
export function getNodeStyles(group: NodeGroup): NodeStyleConfig {
  const styles: Record<NodeGroup, NodeStyleConfig> = {
    trigger: {
      group: 'trigger',
      bgColor: 'var(--node-bg)',
      borderColor: 'var(--node-trigger-border)',
      iconBgColor: 'var(--node-trigger-icon-bg)',
      iconFgColor: 'var(--node-trigger-icon-fg)',
      accentColor: 'var(--node-trigger)',
      handleColor: 'var(--node-handle)',
    },
    transform: {
      group: 'transform',
      bgColor: 'var(--node-bg)',
      borderColor: 'var(--node-transform-border)',
      iconBgColor: 'var(--node-transform-icon-bg)',
      iconFgColor: 'var(--node-transform-icon-fg)',
      accentColor: 'var(--node-transform)',
      handleColor: 'var(--node-handle)',
    },
    flow: {
      group: 'flow',
      bgColor: 'var(--node-bg)',
      borderColor: 'var(--node-flow-border)',
      iconBgColor: 'var(--node-flow-icon-bg)',
      iconFgColor: 'var(--node-flow-icon-fg)',
      accentColor: 'var(--node-flow)',
      handleColor: 'var(--node-handle)',
    },
    ai: {
      group: 'ai',
      bgColor: 'var(--node-bg)',
      borderColor: 'var(--node-ai-border)',
      iconBgColor: 'var(--node-ai-icon-bg)',
      iconFgColor: 'var(--node-ai-icon-fg)',
      accentColor: 'var(--node-ai)',
      handleColor: 'var(--node-handle)',
    },
    action: {
      group: 'action',
      bgColor: 'var(--node-bg)',
      borderColor: 'var(--node-action-border)',
      iconBgColor: 'var(--node-action-icon-bg)',
      iconFgColor: 'var(--node-action-icon-fg)',
      accentColor: 'var(--node-action)',
      handleColor: 'var(--node-handle)',
    },
    output: {
      group: 'output',
      bgColor: 'var(--node-bg)',
      borderColor: 'var(--node-output-border)',
      iconBgColor: 'var(--node-output-icon-bg)',
      iconFgColor: 'var(--node-output-icon-fg)',
      accentColor: 'var(--node-output)',
      handleColor: 'var(--node-handle)',
    },
  };

  return styles[group];
}

/**
 * Calculate handle positions as percentages for vertical distribution
 * Returns array of top percentages for each handle
 *
 * Dynamically adjusts padding based on handle count:
 * - 1-4 handles: 20% padding (comfortable spacing)
 * - 5-8 handles: 15% padding (more compact)
 * - 9+ handles: 12% padding (maximize usable space)
 */
export function calculateHandlePositions(handleCount: number): number[] {
  if (handleCount <= 0) return [];
  if (handleCount === 1) return [50]; // Single handle centered

  // Dynamic padding - reduce for many handles to maximize space
  let padding: number;
  if (handleCount <= 4) {
    padding = 20; // Comfortable spacing for few handles
  } else if (handleCount <= 8) {
    padding = 15; // More compact for medium count
  } else {
    padding = 12; // Maximize space for many handles
  }

  // Distribute handles evenly with dynamic padding from edges
  const availableSpace = 100 - (padding * 2);
  const spacing = availableSpace / (handleCount - 1);

  return Array.from({ length: handleCount }, (_, i) =>
    padding + (i * spacing)
  );
}

/**
 * Get minimap color for a node group
 */
export function getMiniMapColor(group: NodeGroup): string {
  const colors: Record<NodeGroup, string> = {
    trigger: 'var(--node-trigger)',
    transform: 'var(--node-transform)',
    flow: 'var(--node-flow)',
    ai: 'var(--node-ai)',
    action: 'var(--node-action)',
    output: 'var(--node-output)',
  };

  return colors[group];
}

/**
 * Get shape configuration for a node group (subtle differentiation)
 *
 * Border radius order: top-left top-right bottom-right bottom-left
 * - Trigger nodes: more rounded on LEFT (signifies start/entry point)
 * - Output nodes: more rounded on RIGHT (signifies end/exit point)
 */
export function getNodeShapeConfig(group: NodeGroup): NodeShapeConfig {
  const shapes: Record<NodeGroup, NodeShapeConfig> = {
    trigger: {
      borderRadius: '12px',
      accentType: 'none',
    },
    transform: {
      borderRadius: '12px',
      accentType: 'none',
    },
    flow: {
      borderRadius: '12px',
      accentType: 'none',
    },
    ai: {
      borderRadius: '12px',
      accentType: 'none',
    },
    action: {
      borderRadius: '12px',
      accentType: 'none',
    },
    output: {
      borderRadius: '12px',
      accentType: 'none',
    },
  };

  return shapes[group];
}

/**
 * Calculate node dimensions based on handle count (proportional sizing)
 *
 * Handles different node types:
 * - Standard nodes (1 input, 1 output): 64x64 square
 * - If/Switch nodes (multiple outputs): Height grows, width proportional
 * - Many-output nodes (10+): Scales dynamically with balanced aspect ratio
 */
export function calculateNodeDimensions(
  inputCount: number,
  outputCount: number,
): NodeDimensions {
  const baseSize = 64; // Square base for icon-only node
  const maxHandles = Math.max(inputCount, outputCount);

  // Dynamic handle spacing - slightly compress for many handles to prevent overly tall nodes
  // 1-4 handles: 24px, 5-8: 22px, 9+: 20px
  const handleSpacing = maxHandles <= 4 ? 24 : maxHandles <= 8 ? 22 : 20;

  // Calculate height based on max handles (always applies)
  const extraHandles = Math.max(0, maxHandles - 1);
  const baseHeight = baseSize + (extraHandles * handleSpacing);

  // Single handle nodes stay square
  if (maxHandles <= 1) {
    return { width: baseSize, height: baseSize, minWidth: baseSize };
  }

  // Multi-handle nodes (If, Switch, Merge, etc.)
  // Width scales proportionally to maintain a balanced aspect ratio
  // Target aspect ratio: keep width roughly proportional to height
  // 2 handles: 74px, 3: 83px, 4-6: grows gradually, 7+: continues scaling
  let width: number;
  if (maxHandles <= 4) {
    // Small multi-output: modest width growth
    width = baseSize + (extraHandles * 10);
  } else if (maxHandles <= 8) {
    // Medium multi-output: moderate width growth
    // Base + 30 (from first 4) + continued growth
    width = baseSize + 30 + ((maxHandles - 4) * 8);
  } else {
    // Large multi-output (9+ handles): continued proportional growth
    // Base + 30 + 32 (from handles 5-8) + continued growth
    width = baseSize + 30 + 32 + ((maxHandles - 8) * 6);
  }

  // Ensure minimum aspect ratio - width should be at least 40% of height for very tall nodes
  const minWidthFromHeight = Math.ceil(baseHeight * 0.4);
  width = Math.max(width, minWidthFromHeight);

  return { width, height: baseHeight, minWidth: width };
}
