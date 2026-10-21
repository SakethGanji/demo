// Hand-written self-test: proves the run-time fixture enforces the same network policy as explore.
import { test, expect, secret } from '../../src/fixtures.ts';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Username', { exact: true }).fill(secret('username'));
  await page.getByLabel('Password', { exact: true }).fill(secret('password'));
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
});

test('forbidden request from the UI is aborted at run time', async ({ page, policyBlocks }) => {
  await page.getByRole('button', { name: 'Move money', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Money movement' }).getByRole('status')).toHaveText('Request failed');
  expect(policyBlocks.map((b) => `${b.method} ${new URL(b.url).pathname} ${b.reason}`))
    .toContain('POST /api/transfer/quick forbidden-request');
});

test('off-allowlist navigation is aborted at run time', async ({ page, policyBlocks }) => {
  const nav = page.waitForEvent('requestfailed', (r) => r.url().includes('partner-rates.example.net'));
  await page.getByRole('link', { name: 'Partner exchange rates', exact: true }).click();
  const failed = await nav;
  expect(failed.failure()?.errorText).toContain('ERR_BLOCKED_BY_CLIENT');
  expect(policyBlocks.some((b) => b.reason === 'host-not-allowed' && b.url.startsWith('https://partner-rates.example.net/'))).toBe(true);
});

test('API request context is policy-guarded too', async ({ request, policyBlocks }) => {
  await expect(request.post('/api/transfer', { data: { amount: 1 } })).rejects.toThrow(/blocked by test-builder policy/);
  await expect(request.delete('/api/accounts/ACC-1')).rejects.toThrow(/blocked by test-builder policy/);
  await expect(request.get('https://partner-rates.example.net/rates')).rejects.toThrow(/host-not-allowed/);
  expect(policyBlocks).toHaveLength(3);
});
