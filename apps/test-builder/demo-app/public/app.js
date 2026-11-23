// Dashboard behaviour. Everything goes through the JSON API.
const $ = (id) => document.getElementById(id);
const money = (n) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });

async function loadAccounts(q = '') {
  const r = await fetch('/api/accounts' + (q ? '?q=' + encodeURIComponent(q) : ''));
  const rows = await r.json();
  $('acc-body').replaceChildren(...rows.map((a) => {
    const tr = document.createElement('tr');
    for (const v of [a.name, a.number, money(a.balance)]) {
      const td = document.createElement('td'); td.textContent = v; tr.append(td);
    }
    return tr;
  }));
  $('acc-status').textContent = `Showing ${rows.length} account${rows.length === 1 ? '' : 's'}`;
}

async function loadPayees() {
  const rows = await (await fetch('/api/payees')).json();
  $('payee-list').replaceChildren(...rows.map((p) => {
    const li = document.createElement('li'); li.textContent = `${p.name} (${p.accountNumber})`; return li;
  }));
}

let t;
$('search').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => loadAccounts(e.target.value), 150); });

$('payee-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  for (const k of ['name', 'accountNumber', 'type']) $('err-' + k).textContent = '';
  $('payee-status').textContent = '';
  const body = { name: $('payee-name').value, accountNumber: $('payee-acct').value, type: $('payee-type').value };
  const r = await fetch('/api/payees', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json();
  if (r.status === 201) {
    $('payee-status').textContent = `Payee ${j.name} added`;
    e.target.reset();
    loadPayees();
  } else {
    for (const [k, msg] of Object.entries(j.errors || {})) if ($('err-' + k)) $('err-' + k).textContent = msg;
  }
});

const danger = async (method, url, msg) => {
  try {
    const r = await fetch(url, { method, headers: { 'content-type': 'application/json' }, body: method === 'POST' ? '{"amount":5000}' : undefined });
    $('danger-status').textContent = r.ok ? msg : 'Request failed';
  } catch { $('danger-status').textContent = 'Request failed'; }
};
$('transfer').addEventListener('click', () => danger('POST', '/api/transfer', 'Funds transferred'));
$('delete').addEventListener('click', () => danger('DELETE', '/api/accounts/ACC-1', 'Account deleted'));
// Innocuous name, dangerous effect: only the network policy can catch this one.
$('move').addEventListener('click', () => danger('POST', '/api/transfer/quick', 'Money moved'));

loadAccounts();
loadPayees();
