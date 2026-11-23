/**
 * Plain-English readings of a test case for people who do not read JSON: what goes in, what is faked, what must be true.
 * Pure functions; the spec screen and the review screen share them.
 */
import type { Assertion, AssertionOp, Mock, TestCase } from '@/features/workflow-editor/lib/tests';

const OP_WORDS: Record<AssertionOp, string> = {
  eq: 'is', neq: 'is not', gt: 'is greater than', gte: 'is at least', lt: 'is less than', lte: 'is at most',
  contains: 'contains', matches: 'matches the pattern', exists: 'is present', not_exists: 'is not present',
  count: 'has this many results:', type: 'is of type', one_of: 'is one of', range: 'is between', status: 'ends as', shape: 'has the shape',
};

const STATUS_WORDS: Record<string, string> = {
  success: 'finished normally', error: 'failed', not_run: 'did not run', no_output: 'ran but gave nothing back',
};

export function shortValue(v: unknown): string {
  if (v === undefined) return '';
  if (typeof v === 'string') return `"${v}"`;
  if (Array.isArray(v)) return v.map(shortValue).join(', ');
  if (v && typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${shortValue(x)}`).join(', ');
  return String(v);
}

const pathWords = (p: string | undefined) => (p ? p.replace(/\[(\d+)\]/g, ' #$1').replace(/\./g, ' / ') : '');

export function describeAssertion(a: Assertion): string {
  const where = a.node ? `"${a.node}"` : 'the workflow';
  if (a.subject === 'node' || (a.op === 'status' && !a.path)) {
    const status = typeof a.value === 'string' ? (STATUS_WORDS[a.value] ?? a.value) : shortValue(a.value);
    return `${where} ${status}`;
  }
  if (a.op === 'count' && !a.path) {
    return `${a.port ? `The "${a.port}" path of ${where}` : where} gets ${shortValue(a.value)} item${a.value === 1 ? '' : 's'}`;
  }
  const field = a.path ? `"${pathWords(a.path)}"` : 'the result';
  const subject = a.subject === 'request' ? `The request sent by ${where}: ${field}` : `${field} from ${where}`;
  const op = OP_WORDS[a.op] ?? a.op;
  const value = a.op === 'exists' || a.op === 'not_exists' ? '' : ` ${shortValue(a.value)}`;
  return `${subject[0].toUpperCase()}${subject.slice(1)} ${op}${value}`.trim();
}

export function describeMock(step: string, m: Mock): string {
  if ('http' in m) return `${step} answers with status ${m.http.status}${m.http.body !== undefined ? ` and ${shortValue(m.http.body)}` : ''}`;
  if ('error' in m) return `${step} fails with "${m.error.message}"`;
  if ('timeout' in m) return `${step} times out`;
  if ('rules' in m) return `${step} answers differently depending on what it receives`;
  if ('approval' in m && m.approval) return `${step} is ${m.approval.decision} by the reviewer`;
  if (!m.items) return `${step} continues${m.port ? ` on its "${m.port}" path` : ''}`;
  const first = m.items?.[0] as { approval?: { decision?: string } } | undefined;
  if (first?.approval?.decision) return `${step} is ${first.approval.decision} by the reviewer`;
  if (m.port) return `${step} continues on its "${m.port}" path`;
  return `${step} returns ${m.items.length} item${m.items.length === 1 ? '' : 's'}`;
}

// ---- input fields: one level of readable key/value pairs out of the first input item

export interface InputField { path: string; value: string | number | boolean | null }

const SKIP_EMPTY = new Set(['headers', 'query']);

export function flattenInput(item: unknown, prefix = ''): InputField[] {
  if (item === null || typeof item !== 'object' || Array.isArray(item)) {
    return prefix ? [{ path: prefix, value: item as InputField['value'] }] : [];
  }
  const out: InputField[] = [];
  for (const [k, v] of Object.entries(item as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') {
      if (!prefix && SKIP_EMPTY.has(k) && Object.keys(v).length === 0) continue;
      if (Array.isArray(v)) out.push({ path, value: JSON.stringify(v) });
      else out.push(...flattenInput(v, path));
    } else if (!(prefix === '' && k === 'method')) {
      out.push({ path, value: v as InputField['value'] });
    }
  }
  return out;
}

/** An edit typed in a box, read back as the same kind of value the field had. */
export function parseLike(text: string, original: InputField['value']): InputField['value'] {
  if (typeof original === 'number') {
    const n = Number(text);
    return text.trim() !== '' && Number.isFinite(n) ? n : original;
  }
  if (typeof original === 'boolean') return text.trim().toLowerCase() === 'true';
  return text;
}

export function setAtPath<T>(obj: T, path: string, value: unknown): T {
  const [head, ...rest] = path.split('.');
  const base: Record<string, unknown> = obj && typeof obj === 'object' ? { ...(obj as Record<string, unknown>) } : {};
  base[head] = rest.length ? setAtPath(base[head], rest.join('.'), value) : value;
  return base as T;
}

const CATEGORY_WORDS: Record<string, string> = {
  normal: 'Normal case', boundary: 'Edge of a limit', missing: 'Something is missing', wrong_type: 'Wrong kind of value',
  unusual: 'Unusual input', ext_success: 'Outside system answers', ext_error: 'Outside system fails',
  ext_timeout: 'Outside system is too slow', ext_empty: 'Outside system answers with nothing',
};

/** The test's category in words. */
export function categoryWords(t: Pick<TestCase, 'category'>): string {
  return CATEGORY_WORDS[t.category] ?? String(t.category).replace(/_/g, ' ');
}
