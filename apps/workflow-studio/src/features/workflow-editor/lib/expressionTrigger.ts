/**
 * Auto-open rules for the expression suggestions (like n8n): typing `{{ `, `$json.` or `$node["X"].json.`
 * inside the editor opens the list, filtered by what follows the last dot.
 */

export interface ExpressionTrigger {
  /** Index in the text where the typed token starts (replaced on insert). */
  start: number;
  /** The token typed so far, e.g. `$json.al` (empty right after `{{ `). */
  token: string;
  /** True when the cursor already sits inside an open `{{ ... }}`. */
  insideBraces: boolean;
}

const TOKEN_RE = /\$[A-Za-z_]*(?:\["[^"\]]*"\])?(?:\.[A-Za-z_0-9$]*)*$/;
const OPEN_RE = /\{\{\s*$/;

function isInsideBraces(before: string): boolean {
  return before.lastIndexOf('{{') > before.lastIndexOf('}}');
}

/** Returns the trigger at the cursor, or null when the suggestions should stay closed. */
export function detectTrigger(text: string, cursor: number): ExpressionTrigger | null {
  const before = text.slice(0, cursor);
  if (OPEN_RE.test(before)) return { start: cursor, token: '', insideBraces: true };
  const m = TOKEN_RE.exec(before);
  if (!m || !m[0].includes('.')) return null;
  return { start: cursor - m[0].length, token: m[0], insideBraces: isInsideBraces(before) };
}

/** Filters suggestions by the typed token (case-insensitive prefix). An empty token keeps everything. */
export function filterByToken<T extends { label: string }>(items: T[], token: string): T[] {
  if (!token) return items;
  const t = token.toLowerCase();
  return items.filter((i) => i.label.toLowerCase().startsWith(t) && i.label.toLowerCase() !== t);
}

/** Applies a chosen label. Replaces the typed token; wraps in `{{ }}` only when outside braces. */
export function applyTrigger(
  text: string,
  cursor: number,
  trigger: ExpressionTrigger,
  label: string,
): { value: string; cursor: number } {
  const before = text.slice(0, trigger.start);
  const after = text.slice(cursor);
  if (trigger.insideBraces) {
    const ins = label;
    return { value: `${before}${ins}${after}`, cursor: before.length + ins.length };
  }
  const ins = `{{ ${label} }}`;
  return { value: `${before}${ins}${after}`, cursor: before.length + ins.length };
}
