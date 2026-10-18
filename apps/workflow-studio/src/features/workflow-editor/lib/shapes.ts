/**
 * Shape types and pure helpers (CONTRACTS-STAGE2 §1, §3, §4). Mirrors engine `shapes.py`.
 */

export type ShapeType = 'object' | 'array' | 'string' | 'integer' | 'number' | 'boolean' | 'null' | 'unknown';
export type ShapeSource = 'derived' | 'declared' | 'observed' | 'spec' | 'unknown';

export interface Shape {
  type: ShapeType;
  properties?: Record<string, Shape>;
  items?: Shape;
  required?: string[];
  enum?: unknown[];
  format?: string;
  nullable?: boolean;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  'x-source'?: ShapeSource;
}

export interface NodeContract {
  inputs: Record<string, Shape>;
  outputs: Record<string, Shape>;
  error: Shape;
}

export interface NodeContractOverride {
  outputs: Record<string, Shape>;
  /** A user-tightened input shape per port; items arriving at the node are checked against it (CONTRACTS-STAGE3A §4). */
  inputs?: Record<string, Shape>;
  source: 'observed' | 'declared';
  approved_by: string;
  approved_at: string;
  from_execution: string;
}

export type Severity = 'error' | 'warning' | 'info';

export interface Finding {
  code: string; // WF001..WF009
  severity: Severity;
  message: string;
  node: string | null;
  param: string | null;
  path: string | null;
}

export interface Coverage {
  refs_total: number;
  refs_checked: number;
  refs_unknown: number;
  nodes_total: number;
  nodes_known_output: number;
}

export interface CheckResult {
  findings: Finding[];
  shapes_by_node: Record<string, { inputs: Record<string, Shape>; outputs: Record<string, Shape> }>;
  coverage: Coverage;
}

export interface ImportReport {
  mapped: { n8n_name: string; n8n_type: string; node_type: string }[];
  unmapped_nodes: { n8n_name: string; n8n_type: string; reason: string }[];
  untranslated_expressions: { node: string; param: string; expression: string }[];
  disabled_code_nodes: string[];
  findings: Finding[];
}

export const UNKNOWN: Shape = { type: 'unknown', 'x-source': 'unknown' };

/**
 * Follow `path` into `shape`.
 * - `null`: the path definitely does not exist (a known object that lists its properties, without that one).
 * - `UNKNOWN`: cannot tell (unknown shape, object without a property list, scalars, array access by name).
 */
export function lookupShape(shape: Shape | null | undefined, path: (string | number)[]): Shape | null {
  let cur: Shape = shape ?? UNKNOWN;
  for (const key of path) {
    if (cur.type === 'unknown') return UNKNOWN;
    if (cur.type === 'object') {
      if (typeof key !== 'string') return UNKNOWN;
      if (!cur.properties) return UNKNOWN;
      const next = cur.properties[key];
      if (!next) return null;
      cur = next;
    } else if (cur.type === 'array') {
      if (typeof key !== 'number') return UNKNOWN;
      cur = cur.items ?? UNKNOWN;
    } else {
      return UNKNOWN;
    }
  }
  return cur;
}

export interface ShapeField {
  path: string;
  type: ShapeType;
}

/** Flat list of fields for autocomplete: `a`, `a.b`, arrays descend as `a[0].b`. */
export function shapeFields(shape: Shape | null | undefined, maxDepth = 3): ShapeField[] {
  const out: ShapeField[] = [];
  const walk = (s: Shape, prefix: string, depth: number) => {
    if (depth >= maxDepth) return;
    if (s.type === 'object' && s.properties) {
      for (const [name, child] of Object.entries(s.properties)) {
        const p = prefix ? `${prefix}.${name}` : name;
        out.push({ path: p, type: child.type });
        walk(child, p, depth + 1);
      }
    } else if (s.type === 'array' && s.items && prefix) {
      const p = `${prefix}[0]`;
      if (s.items.type === 'object') walk(s.items, p, depth);
    }
  };
  if (shape) walk(shape, '', 0);
  return out;
}
