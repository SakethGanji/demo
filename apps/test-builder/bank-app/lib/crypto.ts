// Northwind Bank (QA target): TOTP (RFC 6238), HS256 JWT, password hashing. No deps.
import { createHmac, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

const B32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

export function base32Decode(s: string): Buffer {
  const clean = s.replace(/=+$/, '').replace(/\s+/g, '').toUpperCase();
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const c of clean) {
    const idx = B32.indexOf(c);
    if (idx < 0) throw new Error('bad base32');
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 0xff); bits -= 8; }
  }
  return Buffer.from(out);
}

export function totp(secretB32: string, at = Date.now(), step = 30, digits = 6): string {
  const counter = Math.floor(at / 1000 / step);
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(counter));
  const h = createHmac('sha1', base32Decode(secretB32)).update(buf).digest();
  const off = h[h.length - 1]! & 0xf;
  const code = ((h.readUInt32BE(off) & 0x7fffffff) % 10 ** digits).toString();
  return code.padStart(digits, '0');
}

/** Accepts the current window and one either side; returns the matched counter (for replay protection). */
export function verifyTotp(secretB32: string, code: string, at = Date.now()): number | null {
  for (const d of [0, -1, 1]) {
    const t = at + d * 30_000;
    if (totp(secretB32, t) === code) return Math.floor(t / 30_000);
  }
  return null;
}

const b64u = (b: Buffer | string) => Buffer.from(b).toString('base64url');

export function signJwt(payload: Record<string, unknown>, key: string, ttlSeconds: number): string {
  const now = Math.floor(Date.now() / 1000);
  const head = b64u(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = b64u(JSON.stringify({ ...payload, iat: now, exp: now + ttlSeconds }));
  const sig = createHmac('sha256', key).update(`${head}.${body}`).digest('base64url');
  return `${head}.${body}.${sig}`;
}

export function verifyJwt(token: string, key: string): Record<string, unknown> | 'expired' | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  const [h, b, s] = parts as [string, string, string];
  const expect = createHmac('sha256', key).update(`${h}.${b}`).digest();
  const got = Buffer.from(s, 'base64url');
  if (got.length !== expect.length || !timingSafeEqual(got, expect)) return null;
  try {
    const head = JSON.parse(Buffer.from(h, 'base64url').toString()) as { alg?: string };
    if (head.alg !== 'HS256') return null;
    const p = JSON.parse(Buffer.from(b, 'base64url').toString()) as Record<string, unknown>;
    if (typeof p.exp === 'number' && p.exp < Date.now() / 1000) return 'expired';
    return p;
  } catch {
    return null;
  }
}

export function hashPassword(pw: string, salt = randomBytes(8).toString('hex')): string {
  return `${salt}:${scryptSync(pw, salt, 32).toString('hex')}`;
}

export function checkPassword(pw: string, stored: string): boolean {
  const [salt, h] = stored.split(':') as [string, string];
  const got = scryptSync(pw, salt, 32);
  const want = Buffer.from(h, 'hex');
  return got.length === want.length && timingSafeEqual(got, want);
}

export const token = (n = 24) => randomBytes(n).toString('base64url');
