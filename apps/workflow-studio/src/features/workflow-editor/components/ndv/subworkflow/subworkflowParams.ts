/** Pure helpers for the ExecuteWorkflow / ExecuteWorkflowTrigger parameters (DESIGN-STAGE8-REUSE sections 2.1, 2.2). */
import type { SubworkflowInputField, VersionSpec } from '../../../lib/subworkflowsApi';

export const FIELD_TYPES = ['string', 'number', 'boolean', 'object', 'array'] as const;
export type FieldType = (typeof FIELD_TYPES)[number];

export interface TriggerField {
  name: string;
  type: FieldType;
  required: boolean;
  description: string;
}

/** The call node's parameters as the engine's `normalize_params` reads them. */
export interface CallParams {
  /** a node with no `version` is a legacy node: it calls the draft and returns one merged item */
  legacy: boolean;
  workflowId: string;
  version: VersionSpec;
  inputMode: 'map' | 'passThrough' | 'custom';
  inputs: Record<string, string>;
  includeItem: boolean;
  mode: 'once' | 'each';
  timeoutSeconds: number;
}

export const DEFAULT_TIMEOUT = 300;
export const MAX_TIMEOUT = 3600;

/** `draft`, `latest` or a version number from whatever the node stored. */
export function parseVersion(raw: unknown): VersionSpec {
  if (typeof raw === 'number' && Number.isInteger(raw) && raw > 0) return raw;
  if (typeof raw === 'string') {
    const v = raw.trim().toLowerCase();
    if (v === 'draft' || v === 'latest') return v;
    if (/^v?\d+$/.test(v)) return Math.max(1, parseInt(v.replace('v', ''), 10));
  }
  return 'latest';
}

export function readCallParams(params: Record<string, unknown> | undefined): CallParams {
  const p = params ?? {};
  const legacy = p.version === undefined || p.version === null || p.version === '';
  const inputMode = p.inputMode === 'map' || p.inputMode === 'custom' || p.inputMode === 'passThrough' ? p.inputMode : legacy ? 'passThrough' : 'map';
  let inputs: Record<string, string> = {};
  if (p.inputs && typeof p.inputs === 'object' && !Array.isArray(p.inputs)) {
    inputs = Object.fromEntries(Object.entries(p.inputs as Record<string, unknown>).map(([k, v]) => [k, v == null ? '' : String(v)]));
  } else if (typeof p.inputs === 'string' && p.inputs.trim().startsWith('{')) {
    try {
      const parsed = JSON.parse(p.inputs) as Record<string, unknown>;
      inputs = Object.fromEntries(Object.entries(parsed).map(([k, v]) => [k, v == null ? '' : String(v)]));
    } catch {
      /* keep {} */
    }
  }
  const t = Number(p.timeoutSeconds);
  return {
    legacy,
    workflowId: typeof p.workflowId === 'string' ? p.workflowId : '',
    version: legacy ? 'draft' : parseVersion(p.version),
    inputMode,
    inputs,
    includeItem: p.includeItem === true || p.includeItem === 'true',
    mode: p.mode === 'each' ? 'each' : 'once',
    timeoutSeconds: Number.isFinite(t) && t > 0 ? Math.min(t, MAX_TIMEOUT) : DEFAULT_TIMEOUT,
  };
}

/** "v3", "latest" or "draft" */
export function versionLabel(v: VersionSpec): string {
  return typeof v === 'number' ? `v${v}` : v;
}

/** Text of the canvas badge: "Refund eligibility · v3" (name omitted until the list has loaded). */
export function badgeText(name: string | undefined, v: VersionSpec): string {
  return name ? `${name} · ${versionLabel(v)}` : versionLabel(v);
}

/** Required child inputs that have no mapping (and no incoming item to fill them). */
export function missingRequired(fields: SubworkflowInputField[], inputs: Record<string, string>, includeItem: boolean): string[] {
  if (includeItem) return [];
  return fields.filter((f) => f.required && !(inputs[f.name] ?? '').trim()).map((f) => f.name);
}

/** The mapping with the keys the child no longer declares dropped (the others keep their expressions). */
export function pruneInputs(inputs: Record<string, string>, fields: SubworkflowInputField[]): Record<string, string> {
  const names = new Set(fields.map((f) => f.name));
  return Object.fromEntries(Object.entries(inputs).filter(([k]) => names.has(k)));
}

export function readTriggerFields(raw: unknown): TriggerField[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((f): f is Record<string, unknown> => !!f && typeof f === 'object')
    .map((f) => ({
      name: typeof f.name === 'string' ? f.name : '',
      type: (FIELD_TYPES as readonly string[]).includes(String(f.type)) ? (f.type as FieldType) : 'string',
      required: f.required === true,
      description: typeof f.description === 'string' ? f.description : '',
    }));
}

/** Indexes of fields whose name is empty or repeats an earlier one. */
export function invalidFieldNames(fields: TriggerField[]): Set<number> {
  const seen = new Set<string>();
  const bad = new Set<number>();
  fields.forEach((f, i) => {
    const n = f.name.trim();
    if (!n || seen.has(n)) bad.add(i);
    seen.add(n);
  });
  return bad;
}

/** Whether a child's io says its contract is typed (declared fields) or open (accepts anything). */
export function contractStatus(io: { input_fields: unknown[]; output_fields: unknown[] } | undefined): 'typed' | 'untyped' {
  if (!io) return 'untyped';
  return io.input_fields.length > 0 || io.output_fields.length > 0 ? 'typed' : 'untyped';
}
