// The closed action vocabulary, its validation, and locator <-> code translation.
import type { Locator, Page } from '@playwright/test';

/** How an element is addressed. Only accessible, user-facing handles; no CSS/XPath. */
export interface ElementRef {
  role?: string;
  name?: string;
  label?: string;
  text?: string;
  exact?: boolean;
  within?: ElementRef;
}

export type Action =
  | { action: 'goto'; url: string; group?: string }
  | { action: 'click'; target: ElementRef; group?: string }
  | { action: 'fill'; target: ElementRef; value: string; group?: string }
  | { action: 'select'; target: ElementRef; option: string; group?: string }
  | { action: 'press'; target?: ElementRef; key: string; group?: string }
  | { action: 'hover'; target: ElementRef; group?: string }
  | { action: 'upload'; target: ElementRef; file: string; group?: string }
  | { action: 'otp'; value: string; within?: ElementRef; group?: string }
  | { action: 'waitFor'; target: ElementRef; state?: 'visible' | 'hidden'; group?: string }
  | { action: 'download'; target: ElementRef; contains?: string[]; group?: string }
  | { action: 'expectVisible'; target: ElementRef; group?: string }
  | { action: 'expectText'; target: ElementRef; text: string; group?: string }
  | { action: 'expectCount'; target: ElementRef; count: number; group?: string }
  | { action: 'done'; reason?: string; success?: boolean };

export const ACTION_NAMES = ['goto', 'click', 'fill', 'select', 'press', 'hover', 'upload', 'otp', 'waitFor', 'download', 'expectVisible', 'expectText', 'expectCount', 'done'] as const;

const ROLES = new Set([
  'alert', 'button', 'cell', 'checkbox', 'columnheader', 'combobox', 'dialog', 'form', 'heading', 'img', 'link',
  'list', 'listitem', 'main', 'navigation', 'option', 'radio', 'region', 'row', 'rowgroup', 'rowheader', 'searchbox',
  'status', 'tab', 'table', 'tabpanel', 'textbox', 'banner', 'menuitem', 'menu', 'switch', 'group', 'paragraph',
]);

function validateRef(r: unknown, where: string): ElementRef {
  if (!r || typeof r !== 'object') throw new Error(`${where}: target must be an object`);
  const o = r as Record<string, unknown>;
  const ref: ElementRef = {};
  for (const k of ['role', 'name', 'label', 'text'] as const) {
    if (o[k] !== undefined) {
      if (typeof o[k] !== 'string') throw new Error(`${where}: target.${k} must be a string`);
      ref[k] = o[k] as string;
    }
  }
  if (o.exact !== undefined) ref.exact = Boolean(o.exact);
  if (ref.role && !ROLES.has(ref.role)) throw new Error(`${where}: unsupported role "${ref.role}"`);
  if (!ref.role && !ref.label && !ref.text) throw new Error(`${where}: target needs role, label or text`);
  if (o.within !== undefined) ref.within = validateRef(o.within, `${where}.within`);
  return ref;
}

/** Strict validation of anything a planner returns. Throws a message the planner can act on. */
export function validateAction(raw: unknown): Action {
  if (!raw || typeof raw !== 'object') throw new Error('action must be a JSON object');
  const o = raw as Record<string, unknown>;
  const a = o.action;
  if (typeof a !== 'string' || !(ACTION_NAMES as readonly string[]).includes(a)) {
    throw new Error(`"action" must be one of ${ACTION_NAMES.join(', ')}`);
  }
  const group = typeof o.group === 'string' ? o.group : undefined;
  const str = (k: string): string => {
    if (typeof o[k] !== 'string' || !(o[k] as string).length) throw new Error(`${a}: "${k}" must be a non-empty string`);
    return o[k] as string;
  };
  switch (a) {
    case 'goto': return { action: 'goto', url: str('url'), group };
    case 'click': return { action: 'click', target: validateRef(o.target, a), group };
    case 'fill': {
      if (typeof o.value !== 'string') throw new Error('fill: "value" must be a string');
      return { action: 'fill', target: validateRef(o.target, a), value: o.value, group };
    }
    case 'select': return { action: 'select', target: validateRef(o.target, a), option: str('option'), group };
    case 'press': return { action: 'press', target: o.target ? validateRef(o.target, a) : undefined, key: str('key'), group };
    case 'hover': return { action: 'hover', target: validateRef(o.target, a), group };
    case 'upload': return { action: 'upload', target: validateRef(o.target, a), file: str('file'), group };
    case 'otp': {
      if (typeof o.value !== 'string' || !o.value) throw new Error('otp: "value" must be a non-empty string (e.g. "{{totp:teller1_totp}}")');
      return { action: 'otp', value: o.value, within: o.within ? validateRef(o.within, `${a}.within`) : undefined, group };
    }
    case 'waitFor': {
      const state = o.state === 'hidden' ? 'hidden' : o.state === 'visible' || o.state === undefined ? 'visible' : undefined;
      if (state === undefined) throw new Error('waitFor: "state" must be "visible" or "hidden"');
      return { action: 'waitFor', target: validateRef(o.target, a), state, group };
    }
    case 'download': {
      if (o.contains !== undefined && (!Array.isArray(o.contains) || o.contains.some((x) => typeof x !== 'string'))) throw new Error('download: "contains" must be an array of strings');
      return { action: 'download', target: validateRef(o.target, a), contains: o.contains as string[] | undefined, group };
    }
    case 'expectVisible': return { action: 'expectVisible', target: validateRef(o.target, a), group };
    case 'expectText': return { action: 'expectText', target: validateRef(o.target, a), text: str('text'), group };
    case 'expectCount': {
      if (typeof o.count !== 'number' || !Number.isInteger(o.count) || o.count < 0) throw new Error('expectCount: "count" must be a non-negative integer');
      return { action: 'expectCount', target: validateRef(o.target, a), count: o.count, group };
    }
    default: return { action: 'done', reason: typeof o.reason === 'string' ? o.reason : undefined, success: o.success !== false };
  }
}

type Scope = Page | Locator;

export function toLocator(page: Page, ref: ElementRef): Locator {
  const scope: Scope = ref.within ? toLocator(page, ref.within) : page;
  const exact = ref.exact ?? true;
  if (ref.role) {
    // getByRole's role param is a string-literal union; ROLES above is a subset of it.
    const role = ref.role as Parameters<Page['getByRole']>[0];
    return ref.name !== undefined ? scope.getByRole(role, { name: ref.name, exact }) : scope.getByRole(role);
  }
  if (ref.label) return scope.getByLabel(ref.label, { exact });
  return scope.getByText(ref.text!, { exact });
}

const q = (s: string) => `'${s.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;

/** The same locator, as readable Playwright source. */
export function locatorCode(ref: ElementRef): string {
  const scope = ref.within ? locatorCode(ref.within) : 'page';
  const exact = ref.exact ?? true;
  if (ref.role) {
    return ref.name !== undefined
      ? `${scope}.getByRole(${q(ref.role)}, { name: ${q(ref.name)}${exact ? ', exact: true' : ''} })`
      : `${scope}.getByRole(${q(ref.role)})`;
  }
  if (ref.label) return `${scope}.getByLabel(${q(ref.label)}${exact ? ', { exact: true }' : ''})`;
  return `${scope}.getByText(${q(ref.text!)}${exact ? ', { exact: true }' : ''})`;
}

export function describeRef(ref: ElementRef): string {
  const base = ref.role ? `${ref.role}${ref.name !== undefined ? ` "${ref.name}"` : ''}` : ref.label ? `field labelled "${ref.label}"` : `text "${ref.text}"`;
  return ref.within ? `${base} in ${describeRef(ref.within)}` : base;
}

export function describeAction(a: Action): string {
  switch (a.action) {
    case 'goto': return `go to ${a.url}`;
    case 'click': return `click ${describeRef(a.target)}`;
    case 'fill': return `fill ${describeRef(a.target)}`;
    case 'select': return `select "${a.option}" in ${describeRef(a.target)}`;
    case 'press': return `press ${a.key}${a.target ? ` in ${describeRef(a.target)}` : ''}`;
    case 'hover': return `hover ${describeRef(a.target)}`;
    case 'upload': return `upload ${a.file} to ${describeRef(a.target)}`;
    case 'otp': return `enter OTP into ${a.within ? describeRef(a.within) : 'the one-time-code fields'}`;
    case 'waitFor': return `wait for ${describeRef(a.target)} to be ${a.state ?? 'visible'}`;
    case 'download': return `download via ${describeRef(a.target)}${a.contains?.length ? ` and assert it contains ${JSON.stringify(a.contains)}` : ''}`;
    case 'expectVisible': return `expect ${describeRef(a.target)} visible`;
    case 'expectText': return `expect ${describeRef(a.target)} to contain "${a.text}"`;
    case 'expectCount': return `expect ${a.count} x ${describeRef(a.target)}`;
    case 'done': return `done${a.reason ? `: ${a.reason}` : ''}`;
  }
}

export { q as quote };
