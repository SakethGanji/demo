// Northwind Bank persistence: node:sqlite file DB, schema + deterministic seed, reset for tests.
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, rmSync } from 'node:fs';
import { dirname } from 'node:path';
import { hashPassword } from './crypto.ts';

export type Role = 'teller' | 'supervisor' | 'auditor';

export interface UserRow { id: number; username: string; password_hash: string; role: Role; totp_secret: string; locale: string; display_name: string; flags: string; payment_limit_cents: number; failed_logins: number; last_totp_counter: number }

/** Mod-97 IBAN check digits for a BBAN. */
export function ibanFor(country: string, bban: string): string {
  const conv = (s: string) => s.toUpperCase().replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55));
  const num = conv(bban + country + '00');
  let rem = 0;
  for (const d of num) rem = (rem * 10 + Number(d)) % 97;
  const check = String(98 - rem).padStart(2, '0');
  return `${country}${check}${bban}`;
}

export function ibanValid(iban: string): boolean {
  const s = iban.replace(/\s+/g, '').toUpperCase();
  if (!/^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$/.test(s)) return false;
  const conv = (s.slice(4) + s.slice(0, 4)).replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55));
  let rem = 0;
  for (const d of conv) rem = (rem * 10 + Number(d)) % 97;
  return rem === 1;
}

export const SEED_USERS = [
  { username: 'teller1', password: 'Teller1-Pass!', role: 'teller', totp: 'JBSWY3DPEHPK3PXP', locale: 'en', name: 'Tess Teller', limit: 1_000_000, flags: { paymentFlow: 'wizard', labs: true, gridDensity: 'comfortable' } },
  { username: 'teller2', password: 'Teller2-Pass!', role: 'teller', totp: 'KRSXG5CTMVRXEZLU', locale: 'en', name: 'Tom Teller', limit: 1_000_000, flags: { paymentFlow: 'quick', labs: false, gridDensity: 'compact' } },
  { username: 'super1', password: 'Super1-Pass!', role: 'supervisor', totp: 'MFRGGZDFMZTWQ2LK', locale: 'en', name: 'Sam Supervisor', limit: 5_000_000, flags: { paymentFlow: 'wizard', labs: true, gridDensity: 'comfortable' } },
  { username: 'audit1', password: 'Audit1-Pass!', role: 'auditor', totp: 'GEZDGNBVGY3TQOJQ', locale: 'de', name: 'Ada Auditor', limit: 0, flags: { paymentFlow: 'wizard', labs: false, gridDensity: 'comfortable' } },
] as const;

const SCHEMA = `
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
  totp_secret TEXT NOT NULL, last_totp_counter INTEGER DEFAULT 0, locale TEXT NOT NULL, display_name TEXT NOT NULL, flags TEXT NOT NULL,
  payment_limit_cents INTEGER NOT NULL, failed_logins INTEGER DEFAULT 0);
CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, holder TEXT NOT NULL, iban TEXT NOT NULL, currency TEXT NOT NULL,
  balance_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', nickname TEXT);
CREATE TABLE payees (id TEXT PRIMARY KEY, name TEXT NOT NULL, iban TEXT NOT NULL, bank TEXT NOT NULL, created_by TEXT);
CREATE TABLE transactions (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, booked_on TEXT NOT NULL, description TEXT NOT NULL,
  category TEXT NOT NULL, amount_cents INTEGER NOT NULL, balance_cents INTEGER NOT NULL);
CREATE TABLE payments (id TEXT PRIMARY KEY, from_account TEXT NOT NULL, payee_id TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL, reference TEXT NOT NULL, execute_on TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL,
  approved_by TEXT, decided_at TEXT, idempotency_key TEXT, request_hash TEXT, channel TEXT NOT NULL DEFAULT 'web', batch_id TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX payments_idem ON payments(created_by, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE bulk_jobs (id TEXT PRIMARY KEY, created_by TEXT NOT NULL, status TEXT NOT NULL, total INTEGER NOT NULL,
  processed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, rows TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE cards (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, last4 TEXT NOT NULL, brand TEXT NOT NULL, token TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, detail TEXT);
`;

/** Deterministic PRNG so the seed (and therefore expected test values) never changes. */
function mulberry32(a: number) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const PAYEE_NAMES = [
  'Acme Supplies Ltd', 'Acme Logistics plc', 'Anglian Water', 'Brighton Council', 'British Gas', 'Bristol Print Co',
  'City Water', 'Contoso Pharma', 'Coventry Building Society', 'Dunmore Farms', 'Eastgate Dental', 'Fabrikam Freight',
  'Greenleaf Energy', 'Harbour Insurance', 'Initech Payroll', 'Jenkins & Sons', 'Kestrel Telecom', 'Lakeside Rentals',
  'Marlow Accountants', 'Northgate Utilities', 'Oakridge School', 'Penrose Medical', 'Quayside Marina', 'Riverside Catering',
  'Stonebridge Motors',
];

export function openDb(file: string): DatabaseSync {
  mkdirSync(dirname(file), { recursive: true });
  const db = new DatabaseSync(file);
  db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;');
  return db;
}

export function resetDb(file: string): DatabaseSync {
  for (const f of [file, `${file}-wal`, `${file}-shm`]) rmSync(f, { force: true });
  const db = openDb(file);
  db.exec(SCHEMA);
  const rnd = mulberry32(20261019);
  const insUser = db.prepare('INSERT INTO users (username, password_hash, role, totp_secret, locale, display_name, flags, payment_limit_cents) VALUES (?,?,?,?,?,?,?,?)');
  for (const u of SEED_USERS) {
    const pw = process.env[`BANK_PW_${u.username.toUpperCase()}`] ?? u.password;
    insUser.run(u.username, hashPassword(pw, `seed-${u.username}`), u.role, u.totp, u.locale, u.name, JSON.stringify(u.flags), u.limit);
  }
  const accounts = [
    { id: 'ACC-1001', name: 'Everyday Checking', holder: 'Jane Smith', bban: 'NWBK60161331926819', bal: 1_245_075 },
    { id: 'ACC-1002', name: 'Business Operating', holder: 'Acme Ltd', bban: 'NWBK60161331926820', bal: 25_000_000 },
    { id: 'ACC-1003', name: 'Instant Saver', holder: 'Mark Jones', bban: 'NWBK60161331926821', bal: 820_000 },
    { id: 'ACC-1004', name: 'Dormant Current', holder: 'Ravi Patel', bban: 'NWBK60161331926822', bal: 0 },
  ];
  const insAcc = db.prepare('INSERT INTO accounts (id, name, holder, iban, currency, balance_cents) VALUES (?,?,?,?,?,?)');
  for (const a of accounts) insAcc.run(a.id, a.name, a.holder, ibanFor('GB', a.bban), 'GBP', a.bal);

  const insPayee = db.prepare('INSERT INTO payees (id, name, iban, bank, created_by) VALUES (?,?,?,?,?)');
  PAYEE_NAMES.forEach((n, i) => insPayee.run(`PAY-${String(i + 1).padStart(3, '0')}`, n, ibanFor('GB', `${i % 3 ? 'BARC' : 'HBUK'}200415${String(10000000 + i * 7919).slice(-8)}`), i % 3 ? 'Barclays' : 'HSBC', 'seed'));

  // 240 transactions on ACC-1001, Jan..Sep 2026, running balance ending at the account balance.
  const cats: [string, string[], number, number][] = [
    ['Groceries', ['Tesco Stores', 'Sainsbury\'s', 'Waitrose', 'Lidl'], -1500, -12000],
    ['Utilities', ['British Gas', 'Anglian Water', 'Kestrel Telecom'], -3000, -15000],
    ['Dining', ['Pret A Manger', 'Wagamama', 'The Red Lion'], -800, -6500],
    ['Travel', ['TfL Travel', 'Trainline', 'Shell Fuel'], -250, -9000],
    ['Salary', ['Initech Payroll'], 320000, 320000],
    ['Rent', ['Lakeside Rentals'], -145000, -145000],
  ];
  const tx: { date: string; desc: string; cat: string; amt: number }[] = [];
  for (let i = 0; i < 240; i++) {
    const month = 1 + Math.floor(i / 27);
    const day = 1 + Math.floor(rnd() * 28);
    const pick = i % 27 === 0 ? 4 : i % 27 === 1 ? 5 : Math.floor(rnd() * 4);
    const [cat, descs, lo, hi] = cats[pick]!;
    const amt = Math.round(lo + (hi - lo) * rnd());
    tx.push({ date: `2026-${String(Math.min(month, 9)).padStart(2, '0')}-${String(day).padStart(2, '0')}`, desc: descs[Math.floor(rnd() * descs.length)]!, cat, amt });
  }
  tx.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  let bal = 1_245_075 - tx.reduce((s, t) => s + t.amt, 0);
  const insTx = db.prepare('INSERT INTO transactions (id, account_id, booked_on, description, category, amount_cents, balance_cents) VALUES (?,?,?,?,?,?,?)');
  tx.forEach((t, i) => { bal += t.amt; insTx.run(`TX-${String(i + 1).padStart(5, '0')}`, 'ACC-1001', t.date, t.desc, t.cat, t.amt, bal); });

  const insPay = db.prepare('INSERT INTO payments (id, from_account, payee_id, amount_cents, currency, reference, execute_on, status, created_by, created_at, channel) VALUES (?,?,?,?,?,?,?,?,?,?,?)');
  insPay.run('PMT-90001', 'ACC-1002', 'PAY-008', 1_500_000, 'GBP', 'Q3 supplier invoice 7781', '2026-10-20', 'pending_approval', 'teller2', '2026-10-18T09:12:00Z', 'web');
  insPay.run('PMT-90002', 'ACC-1001', 'PAY-005', 8_450, 'GBP', 'Gas bill Sept', '2026-10-01', 'completed', 'teller1', '2026-10-01T08:00:00Z', 'web');
  return db;
}
