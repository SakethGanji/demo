/**
 * The transform lens: shape a pipeline and see what it would produce.
 *
 * The interesting decision here is what this panel deliberately does NOT do.
 *
 * `POST /transformations/compile` has two modes. Omit `rows` and it is a
 * schema-only compile: it needs `dataset:read`, touches no data, and answers
 * "what columns would come out". Set `rows` and it becomes a read of the
 * pipeline's *output*, gated behind raw access — because a `compute` step can
 * copy a sensitive column into a new name, so masking by source column name
 * would not hold.
 *
 * This lens only ever asks the first question. That makes it work identically
 * for every seat, which is the whole point: a viewer can design a pipeline and
 * see its shape without ever being handed a value they aren't cleared for.
 * Running it for real is a separate, gated action.
 *
 * What the compile *does* hand back is `step_schemas` — the column set as each
 * step leaves it. That is what turns this from a toggle list into a builder:
 * step N+1's column picker offers the columns that actually exist at step N+1,
 * including ones invented by a `compute` two steps earlier, and every count in
 * the panel is folded schema rather than a guess.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Play, Plus, Trash2, X } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { cn } from '@/shared/lib/utils';
import { errorText } from '@/shared/lib/analyticsClient';
import { Footnote, Identifier, Metric } from '@/shared/components/instrument/Typography';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage } from '@/shared/components/instrument/coverage';
import {
  useCompilePreview,
  useTransformations,
  type OutputColumn,
  type Transformation,
} from '../../hooks/useAnalysis';
import { DtypeChip, LensEmpty, LensError, LensList, Row, Section } from './primitives';
import { fieldClass } from '../fieldStyles';

/* ------------------------------------------------------------------ the ops */

/** Server-side ceilings, mirrored so the panel can show headroom, not just a 422. */
const MAX_STEPS = 50;
const MAX_EXPR_DEPTH = 12;

type StepType =
  | 'select'
  | 'drop'
  | 'rename'
  | 'reorder'
  | 'cast'
  | 'trim'
  | 'case_normalize'
  | 'replace'
  | 'parse_dates'
  | 'split'
  | 'merge'
  | 'compute'
  | 'filter'
  | 'deduplicate'
  | 'sort'
  | 'limit';

/** The whole vocabulary, in the service's own four families. */
const OP_GROUPS: { name: string; ops: StepType[] }[] = [
  { name: 'Columns', ops: ['select', 'drop', 'rename', 'reorder', 'cast'] },
  { name: 'Text and values', ops: ['trim', 'case_normalize', 'replace', 'parse_dates', 'split', 'merge'] },
  { name: 'Derived', ops: ['compute'] },
  { name: 'Rows', ops: ['filter', 'deduplicate', 'sort', 'limit'] },
];

const OP_HINT: Record<StepType, string> = {
  select: 'keep only the listed columns',
  drop: 'remove columns',
  rename: 'old name → new name',
  reorder: 'move columns to the front',
  cast: 'one column → a whitelisted type',
  trim: 'both · left · right',
  case_normalize: 'lower · upper · title',
  replace: 'exact · substring · regex',
  parse_dates: 'strptime format → timestamp',
  split: 'one column → part n of a delimiter',
  merge: 'n columns → one',
  compute: 'expression tree · nine node kinds',
  filter: 'the same 36-operator DSL as queries',
  deduplicate: 'subset · keep first | last | none',
  sort: 'one key · asc | desc',
  limit: 'first n rows',
};

const CAST_TARGETS = [
  'varchar',
  'text',
  'integer',
  'bigint',
  'double',
  'decimal',
  'boolean',
  'date',
  'timestamp',
  'time',
] as const;
type CastTarget = (typeof CAST_TARGETS)[number];

const DATE_PARTS = ['year', 'month', 'day', 'hour', 'minute', 'dow', 'week', 'quarter'] as const;
type DatePart = (typeof DATE_PARTS)[number];

/**
 * The expression shapes this editor can build. The language has ten node kinds;
 * `if` (multi-arm CASE) is the one omitted — a nested WHEN/THEN builder does not
 * fit a 384px dock, and half a conditional builder is worse than none.
 */
const COMPUTE_FNS = [
  'col',
  'lower',
  'upper',
  'trim',
  'length',
  'round',
  'add',
  'sub',
  'mul',
  'div',
  'concat',
  'coalesce',
  'cast',
  'date_extract',
] as const;
type ComputeFn = (typeof COMPUTE_FNS)[number];

type Arity = 'none' | 'scalar' | 'list' | 'pair';

/** All 36 operators of the shared filter DSL, with the arity each one takes. */
const FILTER_OPS: { op: string; arity: Arity }[] = [
  { op: 'eq', arity: 'scalar' },
  { op: 'neq', arity: 'scalar' },
  { op: 'gt', arity: 'scalar' },
  { op: 'gte', arity: 'scalar' },
  { op: 'lt', arity: 'scalar' },
  { op: 'lte', arity: 'scalar' },
  { op: 'in', arity: 'list' },
  { op: 'not_in', arity: 'list' },
  { op: 'between', arity: 'pair' },
  { op: 'not_between', arity: 'pair' },
  { op: 'contains', arity: 'scalar' },
  { op: 'icontains', arity: 'scalar' },
  { op: 'not_contains', arity: 'scalar' },
  { op: 'starts_with', arity: 'scalar' },
  { op: 'ends_with', arity: 'scalar' },
  { op: 'regex', arity: 'scalar' },
  { op: 'len_eq', arity: 'scalar' },
  { op: 'len_gt', arity: 'scalar' },
  { op: 'len_gte', arity: 'scalar' },
  { op: 'len_lt', arity: 'scalar' },
  { op: 'len_lte', arity: 'scalar' },
  { op: 'len_between', arity: 'pair' },
  { op: 'top_n', arity: 'scalar' },
  { op: 'bottom_n', arity: 'scalar' },
  { op: 'top_pct', arity: 'scalar' },
  { op: 'bottom_pct', arity: 'scalar' },
  { op: 'date_before', arity: 'scalar' },
  { op: 'date_after', arity: 'scalar' },
  { op: 'date_between', arity: 'pair' },
  { op: 'last_n_days', arity: 'scalar' },
  { op: 'is_null', arity: 'none' },
  { op: 'is_not_null', arity: 'none' },
  { op: 'is_empty', arity: 'none' },
  { op: 'is_not_empty', arity: 'none' },
  { op: 'is_duplicate', arity: 'none' },
  { op: 'is_unique', arity: 'none' },
];

const arityOf = (op: string): Arity => FILTER_OPS.find((f) => f.op === op)?.arity ?? 'scalar';

/* --------------------------------------------------------------- the draft */

interface Col {
  name: string;
  dtype?: string | null;
}

interface Predicate {
  id: string;
  column: string;
  op: string;
  value: string;
}

interface Base {
  id: string;
}

type Step =
  | (Base & { type: 'select' | 'drop' | 'reorder'; columns: string[] })
  | (Base & { type: 'rename'; column: string; into: string })
  | (Base & { type: 'cast'; column: string; to: CastTarget })
  | (Base & { type: 'trim'; columns: string[]; mode: 'both' | 'left' | 'right' })
  | (Base & { type: 'case_normalize'; columns: string[]; mode: 'lower' | 'upper' | 'title' })
  | (Base & {
      type: 'replace';
      column: string;
      mode: 'exact' | 'substring' | 'regex';
      find: string;
      replaceWith: string;
    })
  | (Base & { type: 'parse_dates'; columns: string[]; format: string })
  | (Base & { type: 'split'; column: string; delimiter: string; index: number; into: string })
  | (Base & { type: 'merge'; columns: string[]; into: string; separator: string })
  | (Base & { type: 'compute'; into: string; fn: ComputeFn; column: string; operand: string })
  | (Base & { type: 'filter'; logic: 'and' | 'or'; predicates: Predicate[] })
  | (Base & { type: 'deduplicate'; columns: string[]; keep: 'first' | 'last' | 'none' })
  | (Base & { type: 'sort'; column: string; direction: 'asc' | 'desc' })
  | (Base & { type: 'limit'; count: number });

let seq = 0;
const nextId = () => `d${(seq += 1)}`;

function newStep(type: StepType, first = ''): Step {
  const id = nextId();
  switch (type) {
    case 'select':
    case 'drop':
    case 'reorder':
      return { id, type, columns: [] };
    case 'rename':
      return { id, type, column: first, into: '' };
    case 'cast':
      return { id, type, column: first, to: 'varchar' };
    case 'trim':
      return { id, type, columns: [], mode: 'both' };
    case 'case_normalize':
      return { id, type, columns: [], mode: 'lower' };
    case 'replace':
      return { id, type, column: first, mode: 'substring', find: '', replaceWith: '' };
    case 'parse_dates':
      return { id, type, columns: [], format: '%Y-%m-%d' };
    case 'split':
      return { id, type, column: first, delimiter: ',', index: 1, into: '' };
    case 'merge':
      return { id, type, columns: [], into: '', separator: ' ' };
    case 'compute':
      return { id, type, into: '', fn: 'col', column: first, operand: '' };
    case 'filter':
      return { id, type, logic: 'and', predicates: [{ id: nextId(), column: first, op: 'eq', value: '' }] };
    case 'deduplicate':
      return { id, type, columns: [], keep: 'first' };
    case 'sort':
      return { id, type, column: first, direction: 'asc' };
    case 'limit':
      return { id, type, count: 1000 };
  }
}

/** A scalar the DSL will accept: numbers and booleans stay typed, the rest is text. */
function coerce(raw: string): unknown {
  const v = raw.trim();
  if (v === 'true') return true;
  if (v === 'false') return false;
  if (v !== '' && !Number.isNaN(Number(v))) return Number(v);
  return raw;
}

function predicatePayload(p: Predicate): Record<string, unknown> | null {
  if (!p.column) return null;
  const arity = arityOf(p.op);
  if (arity === 'none') return { column: p.column, op: p.op };
  if (arity === 'list') {
    const parts = p.value.split(',').map((s) => s.trim()).filter(Boolean);
    return parts.length ? { column: p.column, op: p.op, value: parts.map(coerce) } : null;
  }
  if (arity === 'pair') {
    const parts = p.value.split(',').map((s) => s.trim()).filter(Boolean);
    return parts.length === 2 ? { column: p.column, op: p.op, value: parts.map(coerce) } : null;
  }
  return p.value.trim() === '' ? null : { column: p.column, op: p.op, value: coerce(p.value) };
}

/** The expression tree for a `compute` step, or null while it is incomplete. */
function computeExpr(step: Extract<Step, { type: 'compute' }>): Record<string, unknown> | null {
  if (!step.column) return null;
  const col = { op: 'col', name: step.column };
  const operand = step.operand.trim();
  switch (step.fn) {
    case 'col':
      return col;
    case 'lower':
    case 'upper':
    case 'trim':
    case 'length':
      return { op: 'str', fn: step.fn, value: col };
    case 'round':
      return { op: 'round', value: col, digits: Number(operand) || 0 };
    case 'add':
    case 'sub':
    case 'mul':
    case 'div':
      if (operand === '' || Number.isNaN(Number(operand))) return null;
      return { op: 'arith', fn: step.fn, left: col, right: { op: 'lit', value: Number(operand) } };
    case 'concat':
      if (operand === '') return null;
      return { op: 'concat', parts: [col, { op: 'lit', value: operand }], separator: '' };
    case 'coalesce':
      if (operand === '') return null;
      return { op: 'coalesce', args: [col, { op: 'lit', value: operand }] };
    case 'cast':
      return { op: 'cast', value: col, to: (operand || 'varchar') as CastTarget };
    case 'date_extract':
      return { op: 'date_extract', part: (operand || 'year') as DatePart, value: col };
  }
}

/** Depth of the tree this step will send — the compiler refuses past twelve. */
function exprDepth(step: Extract<Step, { type: 'compute' }>): number {
  return step.fn === 'col' ? 1 : 2;
}

/**
 * A step as the API takes it, or `null` when the draft step is not yet
 * expressible — an empty `drop` is a 422 (`columns` has `min_length=1`), not a
 * no-op, so incomplete steps are withheld from the request rather than sent.
 */
function toPayload(step: Step): Record<string, unknown> | null {
  switch (step.type) {
    case 'select':
    case 'drop':
    case 'reorder':
      return step.columns.length ? { type: step.type, columns: step.columns } : null;
    case 'rename':
      return step.column && step.into.trim()
        ? { type: 'rename', renames: { [step.column]: step.into.trim() } }
        : null;
    case 'cast':
      return step.column ? { type: 'cast', column: step.column, to: step.to } : null;
    case 'trim':
      return step.columns.length ? { type: 'trim', columns: step.columns, mode: step.mode } : null;
    case 'case_normalize':
      return step.columns.length
        ? { type: 'case_normalize', columns: step.columns, mode: step.mode }
        : null;
    case 'replace':
      return step.column && step.find !== ''
        ? {
            type: 'replace',
            column: step.column,
            mode: step.mode,
            find: step.find,
            replace_with: step.replaceWith,
          }
        : null;
    case 'parse_dates':
      return step.columns.length && step.format.trim()
        ? { type: 'parse_dates', columns: step.columns, format: step.format.trim() }
        : null;
    case 'split':
      return step.column && step.delimiter && step.into.trim()
        ? {
            type: 'split',
            column: step.column,
            delimiter: step.delimiter,
            index: Math.max(1, step.index),
            into: step.into.trim(),
          }
        : null;
    case 'merge':
      return step.columns.length >= 2 && step.into.trim()
        ? {
            type: 'merge',
            columns: step.columns,
            into: step.into.trim(),
            separator: step.separator,
          }
        : null;
    case 'compute': {
      const expression = computeExpr(step);
      return expression && step.into.trim()
        ? { type: 'compute', into: step.into.trim(), expression }
        : null;
    }
    case 'filter': {
      const conditions = step.predicates
        .map(predicatePayload)
        .filter((c): c is Record<string, unknown> => c !== null);
      return conditions.length
        ? { type: 'filter', where: { logic: step.logic, conditions } }
        : null;
    }
    case 'deduplicate':
      return {
        type: 'deduplicate',
        subset: step.columns.length ? step.columns : null,
        keep: step.keep,
      };
    case 'sort':
      return step.column
        ? { type: 'sort', by: [{ column: step.column, direction: step.direction }] }
        : null;
    case 'limit':
      return step.count > 0 ? { type: 'limit', count: step.count } : null;
  }
}

/* ------------------------------------------------------- loading a saved one */

const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback);
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
/** A saved operand back into the one text box the editor gives it. */
const valueText = (v: unknown): string =>
  v === null || v === undefined ? '' : Array.isArray(v) ? v.map((x) => String(x)).join(', ') : String(v);

/**
 * A saved step back into the draft, or `null` when this editor cannot represent
 * it faithfully. Returning null is the honest answer: silently dropping a step
 * would change what the pipeline does while claiming to have loaded it, so the
 * panel counts the refusals and says so.
 */
function fromPayload(raw: unknown): Step | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const id = nextId();
  switch (r.type) {
    case 'select':
    case 'drop':
    case 'reorder': {
      const columns = strList(r.columns);
      return columns.length ? { id, type: r.type, columns } : null;
    }
    case 'rename': {
      const renames = (r.renames ?? {}) as Record<string, unknown>;
      const [from] = Object.keys(renames);
      // One pair per step; a multi-column rename would need several cards.
      return from && Object.keys(renames).length === 1
        ? { id, type: 'rename', column: from, into: str(renames[from]) }
        : null;
    }
    case 'cast':
      return str(r.column)
        ? { id, type: 'cast', column: str(r.column), to: (str(r.to, 'varchar') as CastTarget) }
        : null;
    case 'trim': {
      const columns = strList(r.columns);
      return columns.length
        ? { id, type: 'trim', columns, mode: str(r.mode, 'both') as 'both' | 'left' | 'right' }
        : null;
    }
    case 'case_normalize': {
      const columns = strList(r.columns);
      return columns.length
        ? {
            id,
            type: 'case_normalize',
            columns,
            mode: str(r.mode, 'lower') as 'lower' | 'upper' | 'title',
          }
        : null;
    }
    case 'parse_dates': {
      const columns = strList(r.columns);
      return columns.length
        ? { id, type: 'parse_dates', columns, format: str(r.format, '%Y-%m-%d') }
        : null;
    }
    case 'deduplicate':
      return {
        id,
        type: 'deduplicate',
        columns: strList(r.subset),
        keep: str(r.keep, 'first') as 'first' | 'last' | 'none',
      };
    case 'limit':
      return { id, type: 'limit', count: typeof r.count === 'number' ? r.count : 1000 };
    case 'replace':
      return str(r.column)
        ? {
            id,
            type: 'replace',
            column: str(r.column),
            mode: str(r.mode, 'substring') as 'exact' | 'substring' | 'regex',
            find: str(r.find),
            replaceWith: str(r.replace_with),
          }
        : null;
    case 'split':
      return str(r.column) && str(r.delimiter) && str(r.into)
        ? {
            id,
            type: 'split',
            column: str(r.column),
            delimiter: str(r.delimiter),
            index: typeof r.index === 'number' ? r.index : 1,
            into: str(r.into),
          }
        : null;
    case 'merge': {
      const columns = strList(r.columns);
      return columns.length >= 2 && str(r.into)
        ? { id, type: 'merge', columns, into: str(r.into), separator: str(r.separator, ' ') }
        : null;
    }
    case 'filter': {
      // Flat conditions only. A nested group is a shape this editor cannot show,
      // and showing it flattened would change which rows survive.
      const where = (r.where ?? {}) as Record<string, unknown>;
      const conditions = Array.isArray(where.conditions) ? where.conditions : [];
      const predicates: Predicate[] = [];
      for (const c of conditions) {
        if (typeof c !== 'object' || c === null) return null;
        const cc = c as Record<string, unknown>;
        if (!str(cc.column) || !str(cc.op)) return null;
        predicates.push({
          id: nextId(),
          column: str(cc.column),
          op: str(cc.op),
          value: valueText(cc.value),
        });
      }
      return predicates.length
        ? { id, type: 'filter', logic: str(where.logic, 'and') === 'or' ? 'or' : 'and', predicates }
        : null;
    }
    case 'sort': {
      const by = Array.isArray(r.by) ? (r.by[0] as Record<string, unknown> | undefined) : undefined;
      return by && str(by.column)
        ? {
            id,
            type: 'sort',
            column: str(by.column),
            direction: str(by.direction, 'asc') as 'asc' | 'desc',
          }
        : null;
    }
    default:
      return null;
  }
}

/* ------------------------------------------------------------------ summary */

const list = (names: string[], empty: string) => (names.length ? names.join(', ') : empty);

/** The one line a collapsed step shows. */
function summarise(step: Step): string {
  switch (step.type) {
    case 'select':
      return `keep ${list(step.columns, 'nothing yet')}`;
    case 'drop':
      return list(step.columns, 'no columns chosen');
    case 'reorder':
      return `${list(step.columns, 'no columns chosen')} first`;
    case 'rename':
      return `${step.column || '—'} → ${step.into || '—'}`;
    case 'cast':
      return `${step.column || '—'} → ${step.to}`;
    case 'trim':
      return `${step.mode} · ${list(step.columns, 'no columns chosen')}`;
    case 'case_normalize':
      return `${step.mode} · ${list(step.columns, 'no columns chosen')}`;
    case 'replace':
      return `${step.column || '—'} · ${step.mode} · ${step.find || '—'} → ${step.replaceWith || '""'}`;
    case 'parse_dates':
      return `${list(step.columns, 'no columns chosen')} · ${step.format}`;
    case 'split':
      return `${step.column || '—'} · part ${step.index} of "${step.delimiter}" → ${step.into || '—'}`;
    case 'merge':
      return `${list(step.columns, 'no columns chosen')} → ${step.into || '—'}`;
    case 'compute':
      return `${step.into || 'new column'} = ${step.fn}(${step.column || '—'}${step.operand ? `, ${step.operand}` : ''})`;
    case 'filter': {
      const n = step.predicates.length;
      return `${n} predicate${n === 1 ? '' : 's'} · match ${step.logic === 'and' ? 'ALL' : 'ANY'}`;
    }
    case 'deduplicate':
      return `${step.columns.length ? list(step.columns, '') : 'every column'} · keep ${step.keep}`;
    case 'sort':
      return `${step.column || '—'} ${step.direction}`;
    case 'limit':
      return `first ${step.count.toLocaleString()} rows`;
  }
}

/* -------------------------------------------------------------- small parts */

function FieldLabel({ children }: { children: ReactNode }) {
  return <span className="text-footnote text-muted-foreground">{children}</span>;
}

function Picker({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block min-w-0 flex-1">
      <FieldLabel>{label}</FieldLabel>
      <select
        value={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        className={`${fieldClass} mt-0.5 font-mono`}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  mono = true,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  return (
    <label className="block min-w-0 flex-1">
      <FieldLabel>{label}</FieldLabel>
      <input
        value={value}
        aria-label={label}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn(fieldClass, 'mt-0.5', mono && 'font-mono')}
      />
    </label>
  );
}

/**
 * The column picker for a multi-column step.
 *
 * `intent` is the whole reason this is one component rather than two: for
 * `drop` a chosen column is on its way out, so it recedes and strikes through;
 * everywhere else a chosen column is the subject of the step, so it rises. Same
 * control, opposite direction of travel — and neither takes the accent, because
 * a dozen toggles is repeated state (rule 1).
 */
function ColumnChips({
  options,
  selected,
  onToggle,
  intent,
  testid,
}: {
  options: Col[];
  selected: string[];
  onToggle: (name: string) => void;
  intent: 'keep' | 'remove';
  testid: string;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map((c) => {
        const on = selected.includes(c.name);
        return (
          <button
            key={c.name}
            onClick={() => onToggle(c.name)}
            data-testid={testid}
            aria-pressed={on}
            title={c.dtype ?? undefined}
            className={cn(
              'rounded px-1.5 py-0.5 font-mono text-micro transition-colors',
              on && intent === 'keep' && 'bg-[var(--s5)] text-foreground shadow-[var(--hi)]',
              on && intent === 'remove' &&
                'text-muted-foreground line-through shadow-[inset_0_0_0_1px_var(--r1)]',
              !on && 'bg-muted text-muted-foreground hover:text-foreground',
            )}
          >
            {c.name}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A schema chip. New and retyped ride the neutral ramp (--s6 above --s5 above
 * flat), never a hue: schema evolution is magnitude, not identity.
 */
function SchemaChip({ col, mark }: { col: Col; mark: 'new' | 'retyped' | 'same' }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-footnote',
        mark === 'new' && 'bg-[var(--s6)] text-foreground shadow-[var(--hi)]',
        mark === 'retyped' && 'bg-[var(--s5)] text-foreground shadow-[var(--hi)]',
        mark === 'same' && 'bg-muted text-muted-foreground',
      )}
    >
      {col.name}
      <span className={cn(mark === 'same' ? 'text-muted-foreground' : 'text-foreground/70')}>
        {mark === 'new' ? '+ ' : ''}
        {col.dtype ?? '?'}
      </span>
    </span>
  );
}

/** How a column changed between two folded schemas. */
function markOf(col: Col, before: Col[]): 'new' | 'retyped' | 'same' {
  const prev = before.find((c) => c.name === col.name);
  if (!prev) return 'new';
  if (prev.dtype && col.dtype && String(prev.dtype).toLowerCase() !== String(col.dtype).toLowerCase()) {
    return 'retyped';
  }
  return 'same';
}

/* ------------------------------------------------------------ step editors */

function StepEditor({
  step,
  columns,
  onChange,
}: {
  step: Step;
  columns: Col[];
  onChange: (s: Step) => void;
}) {
  const names = columns.map((c) => c.name);

  const toggle = (list_: string[], name: string) =>
    list_.includes(name) ? list_.filter((c) => c !== name) : [...list_, name];

  switch (step.type) {
    case 'select':
    case 'drop':
    case 'reorder':
      return (
        <ColumnChips
          options={columns}
          selected={step.columns}
          intent={step.type === 'drop' ? 'remove' : 'keep'}
          testid={step.type === 'drop' ? 'transform-column-toggle' : 'transform-step-column'}
          onToggle={(n) => onChange({ ...step, columns: toggle(step.columns, n) })}
        />
      );

    case 'rename':
      return (
        <div className="flex gap-1.5">
          <Picker
            label="column"
            value={step.column}
            options={names}
            placeholder="choose…"
            onChange={(v) => onChange({ ...step, column: v })}
          />
          <TextField
            label="new name"
            value={step.into}
            placeholder="new_name"
            onChange={(v) => onChange({ ...step, into: v })}
          />
        </div>
      );

    case 'cast':
      return (
        <div className="flex gap-1.5">
          <Picker
            label="column"
            value={step.column}
            options={names}
            placeholder="choose…"
            onChange={(v) => onChange({ ...step, column: v })}
          />
          <Picker
            label="to"
            value={step.to}
            options={CAST_TARGETS}
            onChange={(v) => onChange({ ...step, to: v as CastTarget })}
          />
        </div>
      );

    case 'trim':
      return (
        <>
          <Picker
            label="mode"
            value={step.mode}
            options={['both', 'left', 'right']}
            onChange={(v) => onChange({ ...step, mode: v as 'both' | 'left' | 'right' })}
          />
          <div className="mt-1.5">
            <ColumnChips
              options={columns}
              selected={step.columns}
              intent="keep"
              testid="transform-step-column"
              onToggle={(n) => onChange({ ...step, columns: toggle(step.columns, n) })}
            />
          </div>
        </>
      );

    case 'case_normalize':
      return (
        <>
          <Picker
            label="mode"
            value={step.mode}
            options={['lower', 'upper', 'title']}
            onChange={(v) => onChange({ ...step, mode: v as 'lower' | 'upper' | 'title' })}
          />
          <div className="mt-1.5">
            <ColumnChips
              options={columns}
              selected={step.columns}
              intent="keep"
              testid="transform-step-column"
              onToggle={(n) => onChange({ ...step, columns: toggle(step.columns, n) })}
            />
          </div>
        </>
      );

    case 'replace':
      return (
        <>
          <div className="flex gap-1.5">
            <Picker
              label="column"
              value={step.column}
              options={names}
              placeholder="choose…"
              onChange={(v) => onChange({ ...step, column: v })}
            />
            <Picker
              label="mode"
              value={step.mode}
              options={['exact', 'substring', 'regex']}
              onChange={(v) => onChange({ ...step, mode: v as 'exact' | 'substring' | 'regex' })}
            />
          </div>
          <div className="mt-1.5 flex gap-1.5">
            <TextField
              label="find"
              value={step.find}
              onChange={(v) => onChange({ ...step, find: v })}
            />
            <TextField
              label="replace with"
              value={step.replaceWith}
              onChange={(v) => onChange({ ...step, replaceWith: v })}
            />
          </div>
        </>
      );

    case 'parse_dates':
      return (
        <>
          <TextField
            label="strptime format"
            value={step.format}
            placeholder="%Y-%m-%d"
            onChange={(v) => onChange({ ...step, format: v })}
          />
          <div className="mt-1.5">
            <ColumnChips
              options={columns}
              selected={step.columns}
              intent="keep"
              testid="transform-step-column"
              onToggle={(n) => onChange({ ...step, columns: toggle(step.columns, n) })}
            />
          </div>
        </>
      );

    case 'split':
      return (
        <>
          <div className="flex gap-1.5">
            <Picker
              label="column"
              value={step.column}
              options={names}
              placeholder="choose…"
              onChange={(v) => onChange({ ...step, column: v })}
            />
            <TextField
              label="delimiter"
              value={step.delimiter}
              onChange={(v) => onChange({ ...step, delimiter: v })}
            />
          </div>
          <div className="mt-1.5 flex gap-1.5">
            <TextField
              label="part (1-based)"
              value={String(step.index)}
              onChange={(v) => onChange({ ...step, index: Math.max(1, Number(v) || 1) })}
            />
            <TextField
              label="into"
              value={step.into}
              placeholder="new_name"
              onChange={(v) => onChange({ ...step, into: v })}
            />
          </div>
        </>
      );

    case 'merge':
      return (
        <>
          <div className="flex gap-1.5">
            <TextField
              label="into"
              value={step.into}
              placeholder="new_name"
              onChange={(v) => onChange({ ...step, into: v })}
            />
            <TextField
              label="separator"
              value={step.separator}
              onChange={(v) => onChange({ ...step, separator: v })}
            />
          </div>
          <div className="mt-1.5">
            <ColumnChips
              options={columns}
              selected={step.columns}
              intent="keep"
              testid="transform-step-column"
              onToggle={(n) => onChange({ ...step, columns: toggle(step.columns, n) })}
            />
            <Footnote className="mt-1">Two or more, in the order you pick them.</Footnote>
          </div>
        </>
      );

    case 'compute': {
      const needs = step.fn;
      const operandControl =
        needs === 'cast' ? (
          <Picker
            label="to"
            value={step.operand || 'varchar'}
            options={CAST_TARGETS}
            onChange={(v) => onChange({ ...step, operand: v })}
          />
        ) : needs === 'date_extract' ? (
          <Picker
            label="part"
            value={step.operand || 'year'}
            options={DATE_PARTS}
            onChange={(v) => onChange({ ...step, operand: v })}
          />
        ) : needs === 'col' || needs === 'lower' || needs === 'upper' || needs === 'trim' || needs === 'length' ? null : (
          <TextField
            label={needs === 'round' ? 'digits' : 'operand'}
            value={step.operand}
            onChange={(v) => onChange({ ...step, operand: v })}
          />
        );

      return (
        <>
          <div className="flex gap-1.5">
            <TextField
              label="into"
              value={step.into}
              placeholder="new_column"
              onChange={(v) => onChange({ ...step, into: v })}
            />
            <Picker
              label="node"
              value={step.fn}
              options={COMPUTE_FNS}
              onChange={(v) => onChange({ ...step, fn: v as ComputeFn })}
            />
          </div>
          <div className="mt-1.5 flex gap-1.5">
            <Picker
              label="column"
              value={step.column}
              options={names}
              placeholder="choose…"
              onChange={(v) => onChange({ ...step, column: v })}
            />
            {operandControl}
          </div>
          {/* The one thing people expect and will not find. */}
          <div className="mt-2 pl-2.5 shadow-[inset_2px_0_0_var(--r2)]">
            <p className="text-footnote leading-relaxed text-muted-foreground">
              An expression sees one row at a time — there are no aggregates in this language, no{' '}
              <span className="font-mono">sum</span>, <span className="font-mono">avg</span> or{' '}
              <span className="font-mono">count</span>, and no window frames. Roll-ups live in the
              Analytics lens.
            </p>
            <Footnote className="mt-1">
              depth {exprDepth(step)} / {MAX_EXPR_DEPTH}
            </Footnote>
          </div>
        </>
      );
    }

    case 'filter':
      return (
        <>
          <div className="flex items-center gap-1.5">
            <FieldLabel>match</FieldLabel>
            {(['and', 'or'] as const).map((l) => (
              <button
                key={l}
                onClick={() => onChange({ ...step, logic: l })}
                aria-pressed={step.logic === l}
                className={cn(
                  'rounded px-1.5 py-0.5 font-mono text-footnote',
                  step.logic === l
                    ? 'bg-[var(--s5)] text-foreground shadow-[var(--hi)]'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {l === 'and' ? 'ALL' : 'ANY'}
              </button>
            ))}
            <button
              onClick={() =>
                onChange({
                  ...step,
                  predicates: [
                    ...step.predicates,
                    { id: nextId(), column: names[0] ?? '', op: 'eq', value: '' },
                  ],
                })
              }
              className="ml-auto rounded px-1.5 py-0.5 text-footnote text-muted-foreground shadow-[inset_0_0_0_1px_var(--r1)] hover:text-foreground"
            >
              + predicate
            </button>
          </div>

          {step.predicates.map((p) => {
            const arity = arityOf(p.op);
            return (
              <div key={p.id} className="mt-1.5 flex items-end gap-1.5" data-testid="transform-predicate">
                <Picker
                  label="column"
                  value={p.column}
                  options={names}
                  placeholder="choose…"
                  onChange={(v) =>
                    onChange({
                      ...step,
                      predicates: step.predicates.map((q) => (q.id === p.id ? { ...q, column: v } : q)),
                    })
                  }
                />
                <Picker
                  label="operator"
                  value={p.op}
                  options={FILTER_OPS.map((f) => f.op)}
                  onChange={(v) =>
                    onChange({
                      ...step,
                      predicates: step.predicates.map((q) => (q.id === p.id ? { ...q, op: v } : q)),
                    })
                  }
                />
                {arity !== 'none' && (
                  <TextField
                    label={arity === 'pair' ? 'low, high' : arity === 'list' ? 'a, b, c' : 'value'}
                    value={p.value}
                    onChange={(v) =>
                      onChange({
                        ...step,
                        predicates: step.predicates.map((q) => (q.id === p.id ? { ...q, value: v } : q)),
                      })
                    }
                  />
                )}
                <button
                  onClick={() =>
                    onChange({ ...step, predicates: step.predicates.filter((q) => q.id !== p.id) })
                  }
                  aria-label="Remove predicate"
                  className="mb-0.5 rounded p-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              </div>
            );
          })}

          <Footnote className="mt-1.5">
            {FILTER_OPS.length} operators — the same vocabulary as query and sampling.
          </Footnote>
        </>
      );

    case 'deduplicate':
      return (
        <>
          <Picker
            label="keep"
            value={step.keep}
            options={['first', 'last', 'none']}
            onChange={(v) => onChange({ ...step, keep: v as 'first' | 'last' | 'none' })}
          />
          <div className="mt-1.5">
            <ColumnChips
              options={columns}
              selected={step.columns}
              intent="keep"
              testid="transform-step-column"
              onToggle={(n) => onChange({ ...step, columns: toggle(step.columns, n) })}
            />
            <Footnote className="mt-1">
              {step.columns.length ? 'Duplicate means these columns match.' : 'No subset — every column must match.'}
            </Footnote>
          </div>
        </>
      );

    case 'sort':
      return (
        <div className="flex gap-1.5">
          <Picker
            label="column"
            value={step.column}
            options={names}
            placeholder="choose…"
            onChange={(v) => onChange({ ...step, column: v })}
          />
          <Picker
            label="direction"
            value={step.direction}
            options={['asc', 'desc']}
            onChange={(v) => onChange({ ...step, direction: v as 'asc' | 'desc' })}
          />
        </div>
      );

    case 'limit':
      return (
        <TextField
          label="rows"
          value={String(step.count)}
          onChange={(v) => onChange({ ...step, count: Math.max(1, Number(v) || 1) })}
        />
      );
  }
}

/* --------------------------------------------------------------- step card */

function StepCard({
  index,
  step,
  columnsIn,
  columnsOut,
  open,
  onOpen,
  onChange,
  onRemove,
  onMove,
  first,
  last,
  incomplete,
}: {
  index: number;
  step: Step;
  columnsIn: Col[];
  columnsOut: Col[] | null;
  open: boolean;
  onOpen: () => void;
  onChange: (s: Step) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  first: boolean;
  last: boolean;
  incomplete: boolean;
}) {
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div
      data-testid="transform-step"
      className={cn(
        'rounded-lg',
        open ? 'bg-[var(--s3)] shadow-[var(--hi)]' : 'bg-card',
      )}
    >
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <button
          onClick={onOpen}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-baseline gap-2 text-left"
        >
          <Identifier className="shrink-0 text-footnote text-muted-foreground">
            {String(index + 1).padStart(2, '0')}
          </Identifier>
          <Identifier className="shrink-0 text-small font-semibold text-foreground">
            {step.type}
          </Identifier>
          <span className="truncate text-micro text-muted-foreground">{summarise(step)}</span>
        </button>

        {columnsOut && (
          <Identifier className="shrink-0 text-footnote text-foreground">
            {columnsIn.length}
            <span className="mx-0.5 text-muted-foreground">▸</span>
            {columnsOut.length}
          </Identifier>
        )}
        {incomplete && !columnsOut && (
          <span className="shrink-0 text-footnote text-muted-foreground">not sent</span>
        )}
        <Chevron className="size-3 shrink-0 text-muted-foreground" />
      </div>

      {open && (
        <div className="px-2 pt-2 pb-2.5 shadow-[inset_0_1px_0_var(--r1)]">
          <StepEditor step={step} columns={columnsIn} onChange={onChange} />

          {columnsOut && (
            <div className="mt-2.5 pt-2 shadow-[inset_0_1px_0_var(--r1)]">
              <p className="mb-1 text-micro font-medium text-foreground">
                Output after this step
              </p>
              <div className="flex flex-wrap gap-1">
                {columnsOut.map((c) => (
                  <SchemaChip key={c.name} col={c} mark={markOf(c, columnsIn)} />
                ))}
              </div>
            </div>
          )}

          <div className="mt-2 flex items-center gap-1">
            <button
              onClick={() => onMove(-1)}
              disabled={first}
              aria-label="Move step earlier"
              className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
            >
              <ArrowUp className="size-3" />
            </button>
            <button
              onClick={() => onMove(1)}
              disabled={last}
              aria-label="Move step later"
              className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
            >
              <ArrowDown className="size-3" />
            </button>
            {incomplete && (
              <Footnote className="ml-1">Incomplete — held back from the compile.</Footnote>
            )}
            <button
              onClick={onRemove}
              aria-label="Remove step"
              data-testid="transform-step-remove"
              className="ml-auto rounded p-1 text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="size-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Connector({ note }: { note?: string }) {
  return (
    <div className="flex h-4 items-center gap-2 pl-3">
      <span className="h-full w-px bg-[var(--r2)]" />
      {note ? <Footnote>{note}</Footnote> : null}
    </div>
  );
}

/* -------------------------------------------------------------- the lens */

interface TransformLensProps {
  datasetId: string | null;
  sheet: string | null;
  /** Columns of the sheet currently on screen; the pipeline's input schema. */
  columns: { name: string; dtype?: string | null }[];
}

export function TransformLens({ datasetId, sheet, columns }: TransformLensProps) {
  const saved = useTransformations(datasetId);
  const compile = useCompilePreview(datasetId);

  // The pipeline opens on a drop step: it is the one operation whose parameters
  // are the columns themselves, so the panel has something to act on with no
  // setup at all.
  const [seed] = useState(() => newStep('drop'));
  const [steps, setSteps] = useState<Step[]>([seed]);
  const [openIds, setOpenIds] = useState<string[]>([seed.id]);
  const [adding, setAdding] = useState(false);
  const [held, setHeld] = useState<{ id: string; name: string; skipped: number } | null>(null);
  /** What the last successful compile actually validated. */
  const [compiled, setCompiled] = useState<{ ids: string[]; json: string } | null>(null);

  const bodies = steps.map((s) => ({ id: s.id, body: toPayload(s) }));
  const live = bodies.filter((b): b is { id: string; body: Record<string, unknown> } => b.body !== null);
  const draftJson = JSON.stringify(live.map((l) => l.body));
  const result = compile.data;

  /**
   * A result describes the draft on screen only while the draft has not moved.
   * Every folded number in this panel is gated on that — a column count from a
   * pipeline you have since edited is a wrong answer stated confidently.
   */
  const fresh = result != null && compiled !== null && compiled.json === draftJson;

  /** Folded schemas, keyed by the draft step that produced them. */
  const stepSchemas = useMemo(() => {
    const m = new Map<string, OutputColumn[]>();
    if (!result || !compiled || !fresh) return m;
    const folded = result.step_schemas ?? [];
    compiled.ids.forEach((id, i) => {
      const cols = folded[i];
      if (cols) m.set(id, cols);
    });
    return m;
  }, [result, compiled, fresh]);

  /** The columns a step actually sees: the last folded schema before it. */
  const schemaBefore = (i: number): Col[] => {
    for (let j = i - 1; j >= 0; j -= 1) {
      const s = stepSchemas.get(steps[j].id);
      if (s) return s;
    }
    return columns;
  };

  const usage = steps.reduce<Record<string, number>>((acc, s) => {
    acc[s.type] = (acc[s.type] ?? 0) + 1;
    return acc;
  }, {});

  const addStep = (type: StepType) => {
    const s = newStep(type, columns[0]?.name ?? '');
    setSteps((prev) => [...prev, s]);
    setOpenIds((prev) => [...prev, s.id]);
    setAdding(false);
  };

  const move = (i: number, dir: -1 | 1) =>
    setSteps((prev) => {
      const j = i + dir;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  const loadSaved = (t: Transformation) => {
    const raw = t.steps ?? [];
    const parsed = raw.map(fromPayload).filter((s): s is Step => s !== null);
    setSteps(parsed.length ? parsed : [newStep('drop')]);
    setOpenIds([]);
    setCompiled(null);
    setHeld({ id: t.id, name: t.name, skipped: raw.length - parsed.length });
  };

  const runCompile = () => {
    const payload = live.map((l) => l.body);
    compile.mutate(
      { sheet, steps: payload },
      { onSuccess: () => setCompiled({ ids: live.map((l) => l.id), json: JSON.stringify(payload) }) },
    );
  };

  const outSchema = result?.output_schema ?? [];
  const outNames = new Set(outSchema.map((c) => c.name));
  const droppedColumns = columns.filter((c) => !outNames.has(c.name));

  const status: { kind: StatusKind; word: string } = compile.error
    ? { kind: 'critical', word: 'refused' }
    : !result
      ? { kind: 'unknown', word: 'not compiled' }
      : !fresh
        ? { kind: 'warning', word: 'draft changed' }
        : { kind: 'good', word: 'compiled' };

  return (
    <>
      {/* First read: how big the pipeline is, what it produces, and whether the
          panel's numbers are still true of the draft on screen. */}
      <div className="mb-4">
        <div className="flex items-start justify-between gap-4">
          <Metric label="Steps" value={steps.length} unit={`/ ${MAX_STEPS}`} size="figure" />
          <Metric
            label="Columns"
            size="figure"
            value={
              <>
                {columns.length}
                <span className="mx-1 text-muted-foreground">▸</span>
                {fresh ? outSchema.length : '—'}
              </>
            }
          />
        </div>
        <MagnitudeBar of={coverage(steps.length, MAX_STEPS)} className="mt-2" />
        <div className="mt-2 flex items-center justify-between gap-2">
          <Status kind={status.kind} className="text-micro">
            {status.word}
          </Status>
          <Footnote>0 rows read · schema only</Footnote>
        </div>
      </div>

      <Section
        title="Pipeline"
        action={
          <div className="flex items-center gap-1">
            {steps.length > 0 && (
              <Button
                size="xs"
                variant="ghost"
                onClick={() => {
                  const s = newStep('drop');
                  setSteps([s]);
                  setOpenIds([s.id]);
                  setCompiled(null);
                  setHeld(null);
                }}
              >
                Reset
              </Button>
            )}
            <Button
              size="xs"
              disabled={compile.isPending || !sheet}
              onClick={runCompile}
              data-testid="transform-compile"
            >
              <Play className="size-3" />
              {compile.isPending ? 'Compiling…' : 'Compile'}
            </Button>
          </div>
        }
      >
        {columns.length === 0 ? (
          <LensEmpty>Select a sheet to shape a pipeline over it.</LensEmpty>
        ) : (
          <>
            {held && (
              <Footnote className="mb-1.5">
                Holding “{held.name}”
                {held.skipped > 0
                  ? ` — ${held.skipped} step${held.skipped === 1 ? '' : 's'} could not be loaded into this editor and ${held.skipped === 1 ? 'is' : 'are'} not in the draft.`
                  : '.'}
              </Footnote>
            )}

            <div className="rounded-lg bg-card px-2 py-1.5">
              <div className="flex items-baseline gap-2">
                <Identifier className="shrink-0 text-small font-semibold text-foreground">
                  source
                </Identifier>
                <span className="truncate text-micro text-muted-foreground">
                  {sheet ?? 'no sheet selected'}
                </span>
                <Identifier className="ml-auto shrink-0 text-footnote text-foreground">
                  {columns.length}
                </Identifier>
              </div>
            </div>

            {steps.map((s, i) => {
              const inCols = schemaBefore(i);
              const outCols = stepSchemas.get(s.id) ?? null;
              return (
                <div key={s.id}>
                  <Connector note={outCols ? `${inCols.length} columns in` : undefined} />
                  <StepCard
                    index={i}
                    step={s}
                    columnsIn={inCols}
                    columnsOut={outCols}
                    open={openIds.includes(s.id)}
                    incomplete={toPayload(s) === null}
                    first={i === 0}
                    last={i === steps.length - 1}
                    onOpen={() =>
                      setOpenIds((prev) =>
                        prev.includes(s.id) ? prev.filter((x) => x !== s.id) : [...prev, s.id],
                      )
                    }
                    onChange={(next) => setSteps((prev) => prev.map((p) => (p.id === s.id ? next : p)))}
                    onRemove={() => setSteps((prev) => prev.filter((p) => p.id !== s.id))}
                    onMove={(dir) => move(i, dir)}
                  />
                </div>
              );
            })}

            <button
              onClick={() => setAdding((v) => !v)}
              disabled={steps.length >= MAX_STEPS}
              data-testid="transform-step-add"
              aria-expanded={adding}
              className="mt-2 flex h-8 w-full items-center justify-center gap-2 rounded-lg text-small text-muted-foreground shadow-[inset_0_0_0_1px_var(--r1)] hover:text-foreground disabled:opacity-40"
            >
              <Plus className="size-3" />
              Add step
              <span className="text-footnote tabular-nums">
                {steps.length} / {MAX_STEPS} used
              </span>
            </button>

            {adding && (
              <div className="mt-2">
                {OP_GROUPS.map((g) => (
                  <div key={g.name} className="mb-2">
                    <p className="mb-1 text-micro font-medium text-foreground">{g.name}</p>
                    {g.ops.map((op) => (
                      <button
                        key={op}
                        onClick={() => addStep(op)}
                        data-testid="transform-op"
                        className="flex w-full items-baseline gap-2 rounded px-1.5 py-1 text-left hover:bg-muted"
                      >
                        <Identifier
                          className={cn(
                            'shrink-0 text-small',
                            usage[op] ? 'font-semibold text-foreground' : 'text-muted-foreground',
                          )}
                        >
                          {op}
                        </Identifier>
                        <span className="truncate text-footnote text-muted-foreground">
                          {OP_HINT[op]}
                        </span>
                        {usage[op] ? (
                          <Identifier className="ml-auto shrink-0 text-footnote text-foreground">
                            {usage[op]}
                          </Identifier>
                        ) : null}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}

            {compile.error && (
              <div className="mt-2">
                <LensError>{errorText(compile.error)}</LensError>
              </div>
            )}
          </>
        )}
      </Section>

      {result && (
        <Section title="Output schema">
          <div data-testid="transform-output-schema">
            <div className="mb-1.5 flex items-baseline justify-between gap-2">
              <span className="text-micro text-muted-foreground tabular-nums">
                {outSchema.length} column{outSchema.length === 1 ? '' : 's'}
                {droppedColumns.length > 0 && ` · ${droppedColumns.length} gone`}
              </span>
              {!fresh && <Footnote>from the previous draft</Footnote>}
            </div>

            {outSchema.map((c) => {
              const mark = markOf(c, columns);
              return (
                <div
                  key={c.name}
                  className={cn(
                    'flex items-center gap-1.5 py-0.5',
                    mark !== 'same' && 'pl-2 shadow-[inset_2px_0_0_var(--r2)]',
                  )}
                  data-testid="transform-output-column"
                >
                  <span
                    className={cn(
                      'truncate font-mono text-micro',
                      mark === 'same' ? 'text-muted-foreground' : 'font-medium text-foreground',
                    )}
                    data-testid="transform-output-name"
                  >
                    {c.name}
                  </span>
                  {mark !== 'same' && (
                    <span className="shrink-0 text-footnote text-muted-foreground">{mark}</span>
                  )}
                  <DtypeChip dtype={c.dtype} className="ml-auto" />
                </div>
              );
            })}

            {droppedColumns.map((c) => (
              <div
                key={c.name}
                className="flex items-center gap-1.5 py-0.5"
                data-testid="transform-dropped-column"
              >
                <span className="truncate font-mono text-micro text-muted-foreground line-through">
                  {c.name}
                </span>
                <span className="ml-auto shrink-0 text-footnote text-muted-foreground">gone</span>
              </div>
            ))}

            {/* Say plainly that no values were fetched — it is the point. */}
            <p className="mt-1.5 text-micro text-muted-foreground/70">
              Schema only — no rows were read, so this works the same for every seat.
            </p>
          </div>
        </Section>
      )}

      <Section title={`Saved pipelines (${saved.data?.items.length ?? 0})`}>
        <LensList
          query={saved}
          items={saved.data?.items ?? []}
          empty="No saved transformations for this dataset."
        >
          {(t) => {
            const count = t.steps?.length ?? 0;
            const holding = held?.id === t.id;
            return (
              <div
                key={t.id}
                data-testid="transformation"
                className={cn(
                  'mb-1 rounded-md px-2 py-1.5',
                  // The pipeline the builder is holding takes value and a left
                  // rule, never the accent: the shell already spends that.
                  holding ? 'bg-[var(--s3)] pl-2.5 shadow-[inset_2px_0_0_var(--r3),var(--hi)]' : 'bg-card',
                )}
              >
                <div className="flex items-baseline gap-1.5">
                  <span
                    className={cn('truncate text-body', holding && 'font-medium text-foreground')}
                  >
                    {t.name}
                  </span>
                  <Identifier className="ml-auto shrink-0 text-footnote text-muted-foreground">
                    {count} step{count === 1 ? '' : 's'}
                  </Identifier>
                </div>
                <div className="mt-0.5 flex items-baseline gap-1.5">
                  <Footnote className="min-w-0 truncate">
                    {t.sheet_key ?? 'sheet not recorded'}
                    {t.description ? ` · ${t.description}` : ''}
                  </Footnote>
                  <button
                    onClick={() => loadSaved(t)}
                    data-testid="transform-load"
                    className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-footnote text-muted-foreground shadow-[inset_0_0_0_1px_var(--r1)] hover:text-foreground"
                  >
                    Load
                  </button>
                </div>
              </div>
            );
          }}
        </LensList>
      </Section>

      <Section title="Limits">
        <Row label="Steps" value={`${steps.length} / ${MAX_STEPS}`} />
        <Row label="Expression depth" value={`max ${MAX_EXPR_DEPTH}`} />
        <Row label="Rows read here" value="0 — never" />
        <Row label="Scope" value={sheet ? `single sheet · ${sheet}` : 'single sheet'} />
        <p className="mt-1.5 text-micro leading-relaxed text-muted-foreground">
          A pipeline reshapes and shrinks one sheet. It cannot widen (no join, union or explode) or
          roll up (no group_by, pivot or window). Joins live in the Relations lens, aggregation in
          Analytics.
        </p>
      </Section>
    </>
  );
}
