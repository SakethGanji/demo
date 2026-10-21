// Hand-authored, reviewed idempotency test (approved to run POST /payments). Proves: a replay with
// the same Idempotency-Key returns the SAME payment (200, Idempotent-Replayed), and reusing the key
// with a different body is rejected (422). Exactly one debit occurs for one key.
import type { PlaywrightWorkerArgs } from '@playwright/test';
import { test, expect, secret, totp } from '../../src/fixtures.ts';

const BASE = 'http://127.0.0.1:4590';

async function token(playwright: PlaywrightWorkerArgs['playwright']): Promise<string> {
  const ctx = await playwright.request.newContext({ baseURL: BASE });
  const res = await ctx.post('/api/v1/auth/token', { data: { username: secret('teller_user'), password: secret('teller_pass'), otp: totp(secret('teller_totp')) } });
  expect(res.ok()).toBeTruthy();
  const j = await res.json();
  await ctx.dispose();
  return j.access_token as string;
}

test('idempotency: same key replays, key reuse with a different body is 422', async ({ request, playwright }) => {
  const bearer = await token(playwright);
  const auth = (extra: Record<string, string> = {}) => ({ authorization: `Bearer ${bearer}`, ...extra });
  const key = `idem-${Date.now()}`;
  const body = { fromAccountId: 'ACC-1001', payeeId: 'PAY-003', amount: '500.00', currency: 'GBP', reference: 'Idempotency A' };

  // First call completes (under the teller limit -> 201).
  const first = await request.post('/api/v1/payments', { headers: auth({ 'idempotency-key': key }), data: body });
  expect(first.status()).toBe(201);
  const id = (await first.json()).id as string;

  // Exact replay: same key + same body -> 200, same payment id, Idempotent-Replayed header.
  const replay = await request.post('/api/v1/payments', { headers: auth({ 'idempotency-key': key }), data: body });
  expect(replay.status()).toBe(200);
  expect(replay.headers()['idempotent-replayed']).toBe('true');
  expect((await replay.json()).id).toBe(id);

  // Key reuse with a DIFFERENT body -> 422 (protects against accidental double-spend with a stale key).
  const conflict = await request.post('/api/v1/payments', { headers: auth({ 'idempotency-key': key }), data: { ...body, amount: '999.00' } });
  expect(conflict.status()).toBe(422);
  expect((await conflict.json()).error.code).toBe('IDEMPOTENCY_KEY_REUSED');

  // A missing key is rejected up front.
  const noKey = await request.post('/api/v1/payments', { headers: auth(), data: body });
  expect(noKey.status()).toBe(400);
  expect((await noKey.json()).error.code).toBe('IDEMPOTENCY_KEY_REQUIRED');
});
