/**
 * Seed realistic demo datasets against a local analytics-service.
 *
 *     node scripts/seed-demo-data.mjs
 *
 * Not test fixtures — the browser suite seeds its own `uitest-` data and sweeps
 * it. These are `demo-` datasets for looking at the UI with something real in
 * it, including the two shapes the design adapts to: a 9-column tidy sheet with
 * a genuinely mixed-type column (R11), and a 189-column risk file with prefix
 * families (R1/R2/R3). Re-running deletes and recreates them.
 */

const API = 'http://localhost:8001/api/v1';
// Quote anything containing a comma, quote or newline. Writing `1,204` raw is
// what collapsed the first attempt's header into a single column.
const cell = (v) => { const t = String(v ?? ''); return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
const toCsv = (cols, rows) => [cols.join(','), ...rows.map(r => cols.map(c => cell(r[c])).join(','))].join('\n');
const ADMIN = '00000000-0000-0000-0000-000000000001';
const H = { 'X-User-Id': ADMIN };

async function j(method, path, body) {
  const r = await fetch(API + path, {
    method,
    headers: { ...H, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const t = r.status === 204 ? '' : await r.text();
  let p = null; try { p = t ? JSON.parse(t) : null } catch { p = t }
  if (r.status >= 400) console.log(`  ! ${method} ${path} -> ${r.status}`, JSON.stringify(p)?.slice(0, 200));
  return { status: r.status, body: p };
}

async function upload(name, csv, datasetId) {
  const fd = new FormData();
  fd.append('file', new Blob([csv], { type: 'text/csv' }), name);
  if (datasetId) fd.append('dataset_id', datasetId);
  const r = await fetch(`${API}/upload?sync=true`, { method: 'POST', headers: H, body: fd });
  const b = await r.json();
  if (r.status >= 400) console.log('  ! upload', r.status, JSON.stringify(b).slice(0, 300));
  return b;
}

// Column metadata is addressed by the stable sheet_key, not the display name.
async function setSensitivity(datasetId, column, businessName, sensitivity) {
  const sheets = await j('GET', `/datasets/${datasetId}/versions/1/sheets`);
  const key = sheets.body?.items?.[0]?.sheet_key;
  if (!key) return console.log('  ! no sheet_key for', datasetId);
  return j('PUT', `/datasets/${datasetId}/sheet-metadata/${encodeURIComponent(key)}/columns/${column}`,
    { business_name: businessName, sensitivity });
}

// Purge previous demo runs so re-running is idempotent.
const existing = await j('GET', '/datasets?limit=200');
for (const d of existing.body?.items ?? []) {
  if (d.name.startsWith('demo-')) await j('DELETE', `/datasets/${d.id}`);
}

const PLANS = ['free', 'pro', 'team', 'enterprise'];
const REGIONS = ['US', 'EU', 'APAC', 'LATAM'];
const STATUS = ['active', 'trialing', 'churned'];
const CAMPAIGNS = ['paid_search_brand', 'webinar_q3', 'summer_promo', 'linkedin_abm',
  'blog_organic', 'retargeting_us', 'product_hunt', 'newsletter_aug', 'partner_co', 'referral_v2'];

// ── 1. The cockpit dataset: signups, with PII and a mixed-type column ──
const signupCols = ['signup_id','email','plan','region','amount','status','signup_date','utm_campaign','mrr_usd'];
const signupRows = [];
for (let i = 0; i < 420; i++) {
  const plan = PLANS[i % 4];
  const amount = plan === 'free' ? 0 : plan === 'pro' ? 290 : plan === 'team' ? 594 : 1499;
  // R11: a genuinely mixed column — ~14% of mrr_usd cannot be parsed as a number.
  const mrr = i % 7 === 0 ? ['N/A', '1,204', '12k', '~500', 'TBC'][i % 5] : String(amount);
  const d = new Date(2026, 6, 1 + (i % 45));
  signupRows.push({ signup_id: `SGN-${84213 - i}`, email: `user${i}@example.com`, plan,
    region: REGIONS[i % 4], amount, status: STATUS[i % 3],
    signup_date: d.toISOString().slice(0, 10), utm_campaign: CAMPAIGNS[i % 10], mrr_usd: mrr });
}
const csv = toCsv(signupCols, signupRows);
const signups = await upload('demo-q3-campaign-signups.csv', csv);
console.log('signups:', signups.dataset_id, signups.row_count, 'rows');

await j('PATCH', `/datasets/${signups.dataset_id}`, {
  description: 'Marketing-site signups for the Q3 acquisition campaign. One row per signup; email is PII.',
  classification: 'confidential',
  domain: 'growth · acquisition',
  source_system: 'manual upload · marketing-site',
});
// Human-declared sensitivity is the ONLY thing that drives masking.
await setSensitivity(signups.dataset_id, 'email', 'Signup email', 'pii');
await j('POST', '/profile', { dataset_id: signups.dataset_id, version_number: 1, sheet: 'data' });

for (const rule of [
  { name: 'sheet is present', rule_type: 'sheet_exists', sheet_selector: 'data', severity: 'error' },
  { name: 'at least 100 signups', rule_type: 'row_count_min', sheet_selector: 'data', parameters: { min: 100 }, severity: 'error' },
  { name: 'email is always present', rule_type: 'not_null', sheet_selector: 'data', column_selector: 'email', severity: 'error' },
  { name: 'signup id is unique', rule_type: 'unique', sheet_selector: 'data', column_selector: 'signup_id', severity: 'error' },
  { name: 'plan is a known tier', rule_type: 'accepted_values', sheet_selector: 'data', column_selector: 'plan', parameters: { values: PLANS }, severity: 'warning' },
  { name: 'campaign is always tagged', rule_type: 'not_null', sheet_selector: 'data', column_selector: 'utm_campaign', severity: 'warning' },
]) await j('POST', `/datasets/${signups.dataset_id}/rules`, rule);
await j('POST', `/datasets/${signups.dataset_id}/versions/1/validate`);
await j('PUT', `/datasets/${signups.dataset_id}/tags`, { tag_name: 'baseline', version_number: 1 });
// A second version, so Versions has a real diff to draw.
const v2Cols = [...signupCols, 'referrer'];
const v2Rows = [];
for (let i = 0; i < 512; i++) {
  const plan = PLANS[i % 4];
  const amount = plan === 'free' ? 0 : plan === 'pro' ? 290 : plan === 'team' ? 594 : 1499;
  const d = new Date(2026, 7, 1 + (i % 30));
  v2Rows.push({ signup_id: `SGN-${90000 - i}`, email: `user${i}@example.com`, plan,
    region: REGIONS[i % 4], amount, status: STATUS[i % 3],
    signup_date: d.toISOString().slice(0, 10), utm_campaign: CAMPAIGNS[i % 10],
    mrr_usd: String(amount), referrer: 'organic' });
}
const v2 = toCsv(v2Cols, v2Rows);
await upload('demo-q3-campaign-signups.csv', v2, signups.dataset_id);
await j('POST', '/profile', { dataset_id: signups.dataset_id, version_number: 2, sheet: 'data' });
await j('POST', `/aggregate`, { dataset_id: signups.dataset_id, version_number: 1, sheet: 'data',
  group_by: ['plan'], aggregations: [{ column: 'amount', function: 'sum', alias: 'total' }] });

// ── 2. The WIDE dataset: 183 columns, to fire R1/R2/R3 ──
const fams = [['dv01', 12], ['cs01', 9], ['vega', 36], ['gamma', 24], ['theta', 8],
  ['pnl', 6], ['xccy', 18], ['basis', 22], ['infl', 14], ['credit', 33]];
let head = ['instrument_id', 'book', 'desk', 'ccy', 'counterparty_lei', 'trade_status', 'quantity'];
for (const [f, n] of fams) for (let i = 1; i <= n; i++) head.push(`${f}_${i}m`);
const wideRows = [];
for (let r = 0; r < 260; r++) {
  const row = { instrument_id: `RT-${40118822 + r}`,
    book: ['RATES-EM','CREDIT-IG','CREDIT-HY','XCCY-USD','VOL-RATES'][r % 5],
    desk: ['NYC-RATES','HKG-RATES','LDN-CRED','FRA-INFL','SGP-XCCY'][r % 5],
    ccy: ['EUR','GBP','JPY','USD','CHF'][r % 5],
    counterparty_lei: `LEI${String(r % 140).padStart(4, '0')}XXNY01`,
    trade_status: ['settled','pending'][r % 2],
    quantity: r % 9 === 0 ? ['N/A','12k','~500','TBC'][r % 4] : String((r + 1) * 100) };
  for (const [f, n] of fams) for (let i = 1; i <= n; i++) row[`${f}_${i}m`] = ((r + i) * 1.37).toFixed(4);
  wideRows.push(row);
}
const wide = toCsv(head, wideRows);
const risk = await upload('demo-eod-risk-exposure.csv', wide);
console.log('wide:', risk.dataset_id, risk.column_count, 'cols');
await j('PATCH', `/datasets/${risk.dataset_id}`, {
  description: 'End-of-day risk exposure by instrument. 183 columns; most are sensitivities.',
  classification: 'restricted', domain: 'rates · credit risk', source_system: 'risk engine',
});
await setSensitivity(risk.dataset_id, 'counterparty_lei', 'Counterparty LEI', 'confidential');
await j('POST', '/profile', { dataset_id: risk.dataset_id, version_number: 1, sheet: 'data' });

// ── 3. A small clean dataset, and a zero-row-ish edge case for contrast ──
const orderRows = [];
for (let i = 0; i < 90; i++)
  orderRows.push({ order_id: `ORD-${1000 + i}`, customer_id: `CUST-${100 + (i % 40)}`,
    region: REGIONS[i % 4], total: (i * 13.5).toFixed(2),
    placed_at: `2026-08-${String((i % 28) + 1).padStart(2, '0')}` });
const orders = toCsv(['order_id','customer_id','region','total','placed_at'], orderRows);
const ord = await upload('demo-regional-orders.csv', orders);
console.log('orders:', ord.dataset_id, ord.row_count, 'rows');
await j('PATCH', `/datasets/${ord.dataset_id}`, { description: 'Order lines by region, one row per line item.', classification: 'internal', domain: 'commerce' });
await j('POST', '/profile', { dataset_id: ord.dataset_id, version_number: 1, sheet: 'data' });
await j('POST', `/datasets/${ord.dataset_id}/rules`, { name: 'order id is unique', rule_type: 'unique', sheet_selector: 'data', column_selector: 'order_id', severity: 'error' });
await j('POST', `/datasets/${ord.dataset_id}/versions/1/validate`);
await j('POST', `/datasets/${ord.dataset_id}/relationships/seed`, {});
await j('POST', `/datasets/${ord.dataset_id}/relationships/suggest`, {});

console.log('\nDEMO IDS');
console.log('  signups', signups.dataset_id);
console.log('  wide   ', risk.dataset_id);
console.log('  orders ', ord.dataset_id);
