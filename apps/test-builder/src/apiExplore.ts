// explore-api: OpenAPI -> Playwright `request` spec (status + response shape), policy-aware.
import { readFileSync } from 'node:fs';
import { quote } from './actions.ts';
import { checkRequest } from './policy.ts';
import type { Target } from './config.ts';

type Schema = {
  $ref?: string; type?: string; properties?: Record<string, Schema>; required?: string[];
  items?: Schema; enum?: unknown[]; example?: unknown; nullable?: boolean;
};
type Op = {
  operationId?: string; summary?: string; security?: unknown[];
  parameters?: { name: string; in: string; required?: boolean; example?: unknown; schema?: Schema }[];
  requestBody?: { content?: Record<string, { schema?: Schema; example?: unknown }> };
  responses?: Record<string, { content?: Record<string, { schema?: Schema }> }>;
};
interface OpenApi { info?: { title?: string; version?: string }; servers?: { url: string }[]; security?: unknown[]; paths: Record<string, Record<string, Op>>; components?: { schemas?: Record<string, Schema> } }

export async function loadOpenApi(src: string, t: Target): Promise<OpenApi> {
  if (/^https?:\/\//.test(src)) {
    const v = checkRequest(t, 'GET', src);
    if (v) throw new Error(`refusing to fetch ${src}: ${v.reason}`);
    const r = await fetch(src);
    if (!r.ok) throw new Error(`GET ${src} -> HTTP ${r.status}`);
    return (await r.json()) as OpenApi;
  }
  return JSON.parse(readFileSync(src, 'utf8')) as OpenApi;
}

function deref(doc: OpenApi, s: Schema | undefined): Schema | undefined {
  let cur = s;
  for (let i = 0; cur?.$ref && i < 10; i++) {
    const name = cur.$ref.replace('#/components/schemas/', '');
    cur = doc.components?.schemas?.[name];
  }
  return cur;
}

function shapeLines(doc: OpenApi, schema: Schema | undefined, expr: string, depth = 0): string[] {
  const s = deref(doc, schema);
  if (!s || depth > 3) return [];
  // A nullable field may legitimately be null; only assert its shape when it is present.
  if (s.nullable) {
    const inner = shapeLines(doc, { ...s, nullable: false }, expr, depth);
    return inner.length ? [`if (${expr} !== null) {`, ...inner.map((l) => `  ${l}`), '}'] : [];
  }
  const out: string[] = [];
  switch (s.type) {
    case 'array': {
      out.push(`expect(Array.isArray(${expr})).toBe(true);`);
      const v = depth === 0 ? 'item' : `item${depth}`;
      const inner = shapeLines(doc, s.items, v, depth + 1);
      if (inner.length) out.push(`for (const ${v} of ${expr}) {`, ...inner.map((l) => `  ${l}`), '}');
      break;
    }
    case 'object': {
      out.push(`expect(${expr}).toEqual(expect.any(Object));`);
      for (const [k, ps] of Object.entries(s.properties ?? {})) {
        const sub = `${expr}.${k}`;
        const lines = shapeLines(doc, ps, sub, depth + 1);
        if (!lines.length) continue;
        if (s.required?.includes(k)) out.push(...lines);
        else out.push(`if (${sub} !== undefined) {`, ...lines.map((l) => `  ${l}`), '}');
      }
      break;
    }
    case 'string':
      out.push(s.enum ? `expect(${JSON.stringify(s.enum)}).toContain(${expr});` : `expect(typeof ${expr}).toBe('string');`);
      break;
    case 'number': case 'integer':
      out.push(`expect(typeof ${expr}).toBe('number');`);
      break;
    case 'boolean':
      out.push(`expect(typeof ${expr}).toBe('boolean');`);
      break;
  }
  return out;
}

function sampleFrom(doc: OpenApi, schema: Schema | undefined): unknown {
  const s = deref(doc, schema);
  if (!s) return {};
  if (s.example !== undefined) return s.example;
  if (s.enum?.length) return s.enum[0];
  switch (s.type) {
    case 'object': return Object.fromEntries(Object.entries(s.properties ?? {}).map(([k, v]) => [k, sampleFrom(doc, v)]));
    case 'array': return [sampleFrom(doc, s.items)];
    case 'number': case 'integer': return 1;
    case 'boolean': return true;
    default: return 'test';
  }
}

function jsonResponse(op: Op, code: string): Schema | undefined {
  return op.responses?.[code]?.content?.['application/json']?.schema;
}

const DANGEROUS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const isSecured = (op: Op, doc: OpenApi): boolean => (op.security ?? doc.security ?? []).length > 0 || doc.security === undefined;

/** Fill a path template from the config's pathParams map; returns null if any param is unknown. */
function fillPath(path: string, pathParams: Record<string, string> = {}): string | null {
  let ok = true;
  const out = path.replace(/\{([^}]+)\}/g, (_all, name: string) => {
    const v = pathParams[name];
    if (v === undefined) { ok = false; return `{${name}}`; }
    return encodeURIComponent(v);
  });
  return ok ? out : null;
}

export function generateApiSpec(doc: OpenApi, t: Target, source: string): { code: string; tests: number; skipped: string[] } {
  const tests: string[] = [];
  const skipped: string[] = [];
  const api = t.api ?? {};
  const pathParams = api.pathParams ?? {};
  const indent = (lines: string[], n: number) => lines.map((l) => (l ? ' '.repeat(n) + l : l));
  const authed = !!api.tokenEndpoint;
  const reqOpt = (extra = '') => (authed ? `{ headers: authHeaders${extra ? ', ' + extra : ''} }` : extra ? `{ ${extra} }` : '');
  const add = (title: string, lines: string[]) => tests.push([`  test(${quote(title)}, async ({ request }) => {`, ...indent(lines, 4), '  });'].join('\n'));
  const call = (m: string, concrete: string, opts: string) => `await request.${m.toLowerCase()}(${quote(concrete)}${opts ? `, ${opts}` : ''});`;

  for (const [path, ops] of Object.entries(doc.paths)) {
    for (const [method, op] of Object.entries(ops)) {
      const M = method.toUpperCase();
      if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(M)) continue;
      const verdict = checkRequest(t, M, t.baseUrl + path.replace(/\{[^}]+\}/g, 'x'));
      if (verdict) { skipped.push(`${M} ${path}: blocked by policy (${verdict.reason}${verdict.rule ? ` ${verdict.rule}` : ''})`); continue; }
      const concrete = fillPath(path, pathParams);
      if (concrete === null) { skipped.push(`${M} ${path}: no sample for path param(s); add api.pathParams`); continue; }
      const okCode = Object.keys(op.responses ?? {}).find((c) => /^2\d\d$/.test(c)) ?? '200';
      const name = op.operationId ? ` (${op.operationId})` : '';
      const okShape = jsonResponse(op, okCode);
      const checkBody = okShape ? ['const body = await res.json();', ...shapeLines(doc, okShape, 'body')] : [];

      if (M === 'GET') {
        add(`GET ${path} -> ${okCode}${name}`, [`const res = ${call('GET', concrete, reqOpt())}`, `expect(res.status()).toBe(${okCode});`, ...checkBody]);
        if (api.unauthTest && authed && isSecured(op, doc)) {
          add(`GET ${path} without auth -> 401${name}`, [`const res = await request.get(${quote(concrete)}, { headers: {} });`, 'expect(res.status()).toBe(401);']);
        }
        continue;
      }
      // Write methods: build a sample body where a schema exists.
      const media = op.requestBody?.content?.['application/json'];
      const valid = media?.example ?? (media?.schema ? sampleFrom(doc, media.schema) : undefined);
      const dataOpt = valid !== undefined ? `data: ${JSON.stringify(valid)}` : '';
      add(`${M} ${path} -> ${okCode}${name}`, [
        ...(DANGEROUS.has(M) ? ['// Mutating call: only enabled because this target+spec is approved (see fixtures policy).'] : []),
        `const res = ${call(M, concrete, reqOpt(dataOpt))}`,
        `expect([${okCode}, 200, 201, 202, 204].filter((v, i, a) => a.indexOf(v) === i)).toContain(res.status());`,
        ...(okShape ? checkBody : []),
      ]);
      const badCode = Object.keys(op.responses ?? {}).find((c) => /^4\d\d$/.test(c));
      if (valid !== undefined && badCode) {
        add(`${M} ${path} invalid body -> ${badCode}${name}`, [
          '// Empty object: violates the request schema.',
          `const res = ${call(M, concrete, reqOpt('data: {}'))}`,
          `expect(res.status()).toBe(${Number(badCode)});`,
        ]);
      }
    }
  }

  const title = `${doc.info?.title ?? 'API'} ${doc.info?.version ?? ''}`.trim();
  // Auth preamble: fetch a bearer token once, using secret()/totp() so no value is inlined.
  let preamble = '';
  if (authed) {
    const body = Object.entries(api.tokenBody ?? {}).map(([field, key]) => {
      const m = /^totp:(\w+)$/.exec(key);
      return `${JSON.stringify(field)}: ${m ? `totp(secret(${quote(m[1]!)}))` : `secret(${quote(key)})`}`;
    }).join(', ');
    preamble = `  let authHeaders: Record<string, string> = {};
  test.beforeAll(async ({ playwright }) => {
    const ctx = await playwright.request.newContext({ baseURL: ${quote(t.baseUrl)} });
    const res = await ctx.post(${quote(api.tokenEndpoint!)}, { data: { ${body} } });
    expect(res.ok(), 'token endpoint should authenticate').toBeTruthy();
    const j = await res.json();
    authHeaders = { authorization: \`Bearer \${j[${quote(api.tokenField ?? 'access_token')}]}\` };
    await ctx.dispose();
  });\n\n`;
  }
  const imports = ['test', 'expect', ...(authed ? ['secret', 'totp'] : [])];
  const code = `// Generated by test-builder explore-api at ${new Date().toISOString()}
// Source:  ${source}
// Target:  ${t.name} (${t.baseUrl}); requests go through the policy-guarded \`request\` fixture.
${skipped.length ? `// Skipped:\n${skipped.map((s) => `//   - ${s}`).join('\n')}\n` : ''}import { ${imports.join(', ')} } from '../../src/fixtures.ts';

test.describe(${quote(title)}, () => {
${preamble}${tests.join('\n\n')}
});
`;
  return { code, tests: tests.length, skipped };
}
