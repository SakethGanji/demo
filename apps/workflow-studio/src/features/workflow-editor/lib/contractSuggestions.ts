/**
 * Autocomplete sources without running (CONTRACTS-STAGE2 §5).
 * Run data wins; when a node has none, the contract shapes from the live check fill in.
 */
import { shapeFields, type Shape, type ShapeType } from './shapes';

export type ShapesByNode = Record<string, { inputs: Record<string, Shape>; outputs: Record<string, Shape> }>;

export interface FieldSuggestion {
  label: string;
  description: string;
  category: 'field';
  source: 'contract';
}

/** Node types whose output is learned from a run (CONTRACTS-STAGE2 §2 "observed"). Single list for the studio. */
export const OBSERVED_NODE_TYPES = ['HttpRequest', 'Postgres', 'MongoDB', 'Neo4j', 'LLMChat', 'AIAgent'] as const;

export function isObservedType(type: string | undefined): boolean {
  return !!type && (OBSERVED_NODE_TYPES as readonly string[]).includes(type);
}

const MAX_PER_SOURCE = 40;

function typeLabel(t: ShapeType): string {
  return t === 'unknown' ? 'any' : t;
}

/** Shape of the current node's input: `main`, else the first listed input. */
export function inputShapeFor(shapesByNode: ShapesByNode, nodeName: string | undefined): Shape | null {
  if (!nodeName) return null;
  const inputs = shapesByNode[nodeName]?.inputs;
  if (!inputs) return null;
  return inputs.main ?? Object.values(inputs)[0] ?? null;
}

export function outputShapeFor(shapesByNode: ShapesByNode, nodeName: string): Shape | null {
  const outputs = shapesByNode[nodeName]?.outputs;
  if (!outputs) return null;
  return outputs.main ?? Object.values(outputs)[0] ?? null;
}

function fromShape(shape: Shape | null, prefix: string): FieldSuggestion[] {
  return shapeFields(shape)
    .slice(0, MAX_PER_SOURCE)
    .map((f) => ({
      label: `${prefix}.${f.path}`,
      description: `${typeLabel(f.type)} field`,
      category: 'field' as const,
      source: 'contract' as const,
    }));
}

const hasItems = (items: unknown[] | undefined) => !!items && items.length > 0;

/**
 * Contract suggestions for the expression editor.
 * - `$json.` fields when the current node's upstream has no run data (`sampleData` empty).
 * - `$node["X"].json.` fields for every node in `shapesByNode` that has no run data in `allNodeData`.
 */
export function contractSuggestions(args: {
  currentNode?: string;
  shapesByNode: ShapesByNode;
  sampleData?: unknown[];
  allNodeData?: Record<string, unknown[]>;
}): FieldSuggestion[] {
  const { currentNode, shapesByNode, sampleData, allNodeData } = args;
  const out: FieldSuggestion[] = [];
  if (!hasItems(sampleData)) out.push(...fromShape(inputShapeFor(shapesByNode, currentNode), '$json'));
  for (const name of Object.keys(shapesByNode)) {
    if (name === currentNode || hasItems(allNodeData?.[name])) continue;
    out.push(...fromShape(outputShapeFor(shapesByNode, name), `$node["${name}"].json`));
  }
  return out;
}
