// Policy layer. Enforced by the builder around the browser, never delegated to the planner.
import type { BrowserContext, Locator } from '@playwright/test';
import type { Target } from './config.ts';

export interface NetworkBlock {
  kind: 'network';
  at: string;
  method: string;
  url: string;
  reason: 'host-not-allowed' | 'forbidden-request';
  rule?: string;
}

export interface ActionBlock {
  kind: 'action';
  at: string;
  role: string;
  name: string;
  rule: string;
  action: string;
}

export type PolicyBlock = NetworkBlock | ActionBlock;

/** Pure decision: should this request be aborted? */
export function checkRequest(t: Target, method: string, rawUrl: string): Omit<NetworkBlock, 'at' | 'kind' | 'method' | 'url'> | null {
  let u: URL;
  try { u = new URL(rawUrl); } catch { return { reason: 'host-not-allowed' }; }
  if (u.protocol === 'data:' || u.protocol === 'blob:' || u.protocol === 'about:') return null;
  const host = u.host.toLowerCase();
  if (!t.allowedHosts.includes(host) && !t.allowedHosts.includes(u.hostname.toLowerCase())) {
    return { reason: 'host-not-allowed' };
  }
  const m = method.toUpperCase();
  for (const rule of t.forbiddenRequests) {
    if ((rule.method === '*' || rule.method === m) && rule.path.test(u.pathname)) {
      return { reason: 'forbidden-request', rule: rule.raw };
    }
  }
  return null;
}

/** Install the network policy on a context: abort off-allowlist hosts and forbidden requests. */
export async function installNetworkPolicy(
  context: BrowserContext,
  t: Target,
  onBlock: (b: NetworkBlock) => void,
): Promise<void> {
  await context.route('**/*', async (route) => {
    const req = route.request();
    const verdict = checkRequest(t, req.method(), req.url());
    if (verdict) {
      onBlock({ kind: 'network', at: new Date().toISOString(), method: req.method(), url: req.url(), ...verdict });
      await route.abort('blockedbyclient');
      return;
    }
    await route.continue();
  });
}

/** Pure decision on an element's computed role + accessible name. */
export function checkAction(t: Target, role: string, name: string): string | null {
  const hay = `${role} ${name}`;
  for (const re of t.forbiddenActions) {
    re.lastIndex = 0;
    if (re.test(name) || re.test(hay)) return String(re);
  }
  return null;
}

/**
 * Read what the element REALLY is from the browser (not what the planner claims): its aria
 * snapshot gives computed role + accessible name; inner text is checked too, as a fallback.
 */
export async function describeElement(loc: Locator): Promise<{ role: string; name: string; text: string }> {
  const snap = await loc.ariaSnapshot({ timeout: 5000 }).catch(() => '');
  const first = snap.split('\n')[0] ?? '';
  const m = /^-\s+([\w-]+)(?:\s+"((?:[^"\\]|\\.)*)")?/.exec(first);
  const text = (await loc.innerText({ timeout: 2000 }).catch(() => '')).trim().slice(0, 200);
  const aria = (await loc.getAttribute('aria-label', { timeout: 2000 }).catch(() => null)) ?? '';
  return { role: m?.[1] ?? 'unknown', name: m?.[2] ?? (aria || text), text };
}

export async function checkElement(t: Target, loc: Locator): Promise<{ role: string; name: string; rule: string | null }> {
  const d = await describeElement(loc);
  const rule = checkAction(t, d.role, d.name) ?? checkAction(t, d.role, d.text);
  return { role: d.role, name: d.name, rule };
}
