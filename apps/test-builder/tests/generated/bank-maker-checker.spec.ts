// Hand-authored, human-reviewed maker-checker test. It performs REAL mutations (POST /payments,
// approve) that exploration is forbidden to do, so it only runs once `tb approve`'d (see fixtures
// policy). Proves: over-limit payment -> pending; supervisor approves in a separate session; the
// maker cannot approve their own payment (four-eyes).
import type { PlaywrightWorkerArgs } from '@playwright/test';
import { test, expect, secret, totp } from '../../src/fixtures.ts';

const BASE = 'http://127.0.0.1:4590';

async function token(playwright: PlaywrightWorkerArgs['playwright'], u: string, p: string, s: string): Promise<string> {
  const ctx = await playwright.request.newContext({ baseURL: BASE });
  const res = await ctx.post('/api/v1/auth/token', { data: { username: u, password: p, otp: totp(s) } });
  expect(res.ok(), `token for ${u}`).toBeTruthy();
  const j = await res.json();
  await ctx.dispose();
  return j.access_token as string;
}

test('maker-checker: over-limit payment is approved by a different person; self-approval is forbidden', async ({ request, playwright }) => {
  const teller = await token(playwright, secret('teller_user'), secret('teller_pass'), secret('teller_totp'));
  const supervisor = await token(playwright, secret('super_user'), secret('super_pass'), secret('super_totp'));

  // Maker: a payment above the teller's limit goes to pending_approval (202), not completed.
  const created = await request.post('/api/v1/payments', {
    headers: { authorization: `Bearer ${teller}`, 'idempotency-key': `mk-${Date.now()}` },
    data: { fromAccountId: 'ACC-1002', payeeId: 'PAY-001', amount: '12500.00', currency: 'GBP', reference: 'Maker-checker test' },
  });
  expect(created.status()).toBe(202);
  const payment = await created.json();
  expect(payment.status).toBe('pending_approval');

  // Checker: a supervisor (a different person) approves the teller's payment -> four-eyes satisfied.
  const approved = await request.post(`/api/v1/payments/${payment.id}/approve`, { headers: { authorization: `Bearer ${supervisor}` } });
  expect(approved.status()).toBe(200);
  expect((await approved.json()).status).toBe('approved');

  // Self-approval: the supervisor raises their own over-limit payment and may NOT approve it.
  const own = await request.post('/api/v1/payments', {
    headers: { authorization: `Bearer ${supervisor}`, 'idempotency-key': `mk-own-${Date.now()}` },
    data: { fromAccountId: 'ACC-1002', payeeId: 'PAY-002', amount: '60000.00', currency: 'GBP', reference: 'Own payment' },
  });
  expect(own.status()).toBe(202);
  const self = await request.post(`/api/v1/payments/${(await own.json()).id}/approve`, { headers: { authorization: `Bearer ${supervisor}` } });
  expect(self.status()).toBe(403);
  expect((await self.json()).error.code).toBe('SELF_APPROVAL');
});
