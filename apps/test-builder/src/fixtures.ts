// Run-time fixture used by every generated spec: applies the target's network policy to the
// browser context AND to the API `request` context, records a HAR per test, and provides secret().
import { test as base, expect, type APIRequestContext, type Page, type Locator } from '@playwright/test';
import { dirname, join, isAbsolute, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { loadTarget } from './config.ts';
import { installNetworkPolicy, checkRequest, type NetworkBlock } from './policy.ts';
import { totp as totpFn } from './totp.ts';
import { extractText } from './downloads.ts';
import { isApproved } from './approvals.ts';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, '..');
export const target = loadTarget(process.env.TB_TARGET ?? join(here, '..', 'targets', 'demo.json'));

/** Current RFC 6238 TOTP code from a base32 secret (resolved via secret('<key>')). */
export function totp(secretBase32: string): string { return totpFn(secretBase32); }

/** Resolve an upload fixture path relative to the target config's directory. */
export function filePath(p: string): string { return isAbsolute(p) ? p : resolve(dirname(target.path), p); }

/** Fill a group of single-character one-time-code inputs (or a single box) with `code`.
 *  Waits for the fields to render (a code screen usually follows an async step). */
export async function enterOtp(boxes: Locator, code: string): Promise<void> {
  await boxes.first().waitFor({ state: 'visible' });
  let n = 0;
  for (let i = 0; i < 50 && n !== code.length && n !== 1; i++) { n = await boxes.count(); if (n >= code.length || n === 1) break; await boxes.page().waitForTimeout(100); }
  n = await boxes.count();
  if (n >= code.length) { for (let i = 0; i < code.length; i++) await boxes.nth(i).fill(code[i]!); }
  else if (n === 1) await boxes.first().fill(code);
  else throw new Error(`enterOtp: ${n} field(s) for a ${code.length}-digit code`);
}

/** Click `trigger`, capture the download, and assert the file contains each of `contains` (PDF text is extracted). */
export async function expectDownload(page: Page, trigger: Locator, contains: string[] = []): Promise<string> {
  const [dl] = await Promise.all([page.waitForEvent('download'), trigger.click()]);
  const dest = join(process.env.TB_RUN_DIR ?? '.', 'downloads', dl.suggestedFilename());
  await dl.saveAs(dest);
  if (contains.length) {
    const text = extractText(dest);
    for (const s of contains) expect(text, `download ${dl.suggestedFilename()} should contain ${JSON.stringify(s)}`).toContain(s);
  }
  return dest;
}

/** Credential value from env, by its key in the target config. Never inline values in specs. */
export function secret(key: string): string {
  const spec = target.credentials[key];
  if (!spec) throw new Error(`secret("${key}"): not declared in target config ${target.path}`);
  const v = process.env[spec.env];
  if (!v) throw new Error(`secret("${key}"): env var ${spec.env} is not set`);
  return v;
}

const API_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'fetch']);

function guardRequest(req: APIRequestContext, baseURL: string, blocks: NetworkBlock[], policyTarget: typeof target): APIRequestContext {
  return new Proxy(req, {
    get(obj, prop, recv) {
      const v = Reflect.get(obj, prop, recv) as unknown;
      if (typeof prop !== 'string' || typeof v !== 'function') return v;
      if (!API_METHODS.has(prop)) return (v as (...a: unknown[]) => unknown).bind(obj);
      return async (urlOrReq: string | { url(): string; method(): string }, opts?: { method?: string }) => {
        const url = typeof urlOrReq === 'string' ? urlOrReq : urlOrReq.url();
        const method = prop === 'fetch'
          ? (opts?.method ?? (typeof urlOrReq === 'string' ? 'GET' : urlOrReq.method()))
          : prop.toUpperCase();
        const abs = new URL(url, baseURL.replace(/\/?$/, '/')).toString();
        const verdict = checkRequest(policyTarget, method, abs);
        if (verdict) {
          blocks.push({ kind: 'network', at: new Date().toISOString(), method, url: abs, ...verdict });
          throw new Error(`blocked by test-builder policy: ${method} ${abs} (${verdict.reason}${verdict.rule ? ` ${verdict.rule}` : ''})`);
        }
        return (v as (...a: unknown[]) => unknown).call(obj, urlOrReq, opts);
      };
    },
  });
}

export const test = base.extend<{ policyBlocks: NetworkBlock[] }>({
  policyBlocks: async ({}, use, testInfo) => {
    const blocks: NetworkBlock[] = [];
    await use(blocks);
    if (blocks.length) {
      await testInfo.attach('policy-blocks.json', { body: JSON.stringify(blocks, null, 2), contentType: 'application/json' });
    }
  },
  contextOptions: async ({ contextOptions }, use, testInfo) => {
    const statePath = target.auth?.storageState ? resolve(dirname(target.path), target.auth.storageState) : undefined;
    await use({
      ...contextOptions,
      // Reuse a pre-authenticated session (login/SSO handled outside the tool) when the file exists.
      ...(statePath && existsSync(statePath) ? { storageState: statePath } : {}),
      recordHar: process.env.TB_HAR === '0' ? undefined : { path: testInfo.outputPath('network.har'), mode: 'full' },
    });
  },
  context: async ({ context, policyBlocks }, use, testInfo) => {
    await installNetworkPolicy(context, effectiveTarget(testInfo.file), (b) => policyBlocks.push(b));
    await use(context);
  },
  request: async ({ request, policyBlocks, baseURL }, use, testInfo) => {
    await use(guardRequest(request, baseURL ?? target.baseUrl, policyBlocks, effectiveTarget(testInfo.file)));
  },
});

/**
 * The policy a given spec runs under. An APPROVED spec keeps the host allowlist but drops
 * forbiddenRequests, so a vetted test may perform a mutation that exploration would have blocked.
 */
function effectiveTarget(specFile: string): typeof target {
  if (target.forbiddenRequests.length && isApproved(specFile, ROOT)) {
    return { ...target, forbiddenRequests: [] };
  }
  return target;
}

export { expect };
