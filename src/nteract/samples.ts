/** Realistic sample payloads for the nteract gallery: Jupyter outputs, MIME bundles, notebook users. */
import type { JupyterOutput } from '@/components/cell/OutputArea'
import { GIF, JPEG, PNG, WEBP } from './media'

export { GIF, JPEG, PNG, WEBP }

export const DATAFRAME_HTML = `<div><style scoped>.dataframe{border-collapse:collapse;font:12.5px system-ui}.dataframe th,.dataframe td{padding:4px 10px;text-align:right;border-bottom:1px solid rgb(0 0 0 / .08)}.dataframe thead th{font-weight:600}.dataframe tbody tr:nth-child(odd){background:rgb(82 80 233 / .05)}</style>
<table border="1" class="dataframe"><thead><tr><th></th><th>region</th><th>accounts</th><th>mrr</th><th>churn</th></tr></thead>
<tbody><tr><th>0</th><td>EMEA</td><td>1,204</td><td>$412,880</td><td>2.1%</td></tr><tr><th>1</th><td>NA</td><td>2,931</td><td>$1,102,400</td><td>1.7%</td></tr><tr><th>2</th><td>APAC</td><td>866</td><td>$288,150</td><td>3.4%</td></tr><tr><th>3</th><td>LATAM</td><td>402</td><td>$96,720</td><td>4.0%</td></tr></tbody></table>
<p style="font:12px system-ui;opacity:.7">4 rows × 4 columns</p></div>`

export const MARKDOWN = `### Weekly revenue review

Revenue grew **12.4%** quarter on quarter, led by *NA*. Churn is flat.

| region | mrr | Δ |
|---|---:|---:|
| NA | $1.10M | +9% |
| EMEA | $0.41M | +14% |

- [x] refresh the \`accounts\` extract
- [ ] review APAC pricing

Inline math $\\sigma = \\sqrt{\\tfrac{1}{n}\\sum (x_i-\\mu)^2}$ and a block:

$$\\text{NRR} = \\frac{\\text{MRR}_{t} - \\text{churn}}{\\text{MRR}_{t-1}}$$

\`\`\`python
df.groupby("region").mrr.sum().plot.bar()
\`\`\`
`

export const SVG_CHART = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="150" viewBox="0 0 360 150" font-family="system-ui" font-size="11">
<rect width="360" height="150" rx="10" fill="#f4f6fb"/>
<g fill="#5250e9">${[52, 80, 64, 104, 92, 120].map((h, i) => `<rect x="${28 + i * 54}" y="${130 - h}" width="34" height="${h}" rx="4" opacity="${0.55 + i * 0.08}"/>`).join('')}</g>
<polyline fill="none" stroke="#16a34a" stroke-width="2.5" points="${[52, 80, 64, 104, 92, 120].map((h, i) => `${45 + i * 54},${122 - h}`).join(' ')}"/>
<text x="16" y="20" fill="#0d1430" font-weight="600">image/svg+xml · matplotlib-style figure</text></svg>`

export const JSON_DATA = {
  dataset: 'accounts', version: 7, rows: 12408, refreshed_at: '2026-10-31T08:45:00Z',
  schema: [{ name: 'id', type: 'int64', nullable: false }, { name: 'email', type: 'string', masked: true }, { name: 'mrr', type: 'float64' }],
  tags: ['finance', 'q3'], owner: { team: 'growth', on_call: null }, sampled: false,
}

const ESC = '\u001b'
export const ANSI_LOG = `${ESC}[1mpytest${ESC}[0m tests/test_revenue.py\n${ESC}[32m✓${ESC}[0m test_totals ${ESC}[2m(12ms)${ESC}[0m\n${ESC}[32m✓${ESC}[0m test_regions ${ESC}[2m(8ms)${ESC}[0m\n${ESC}[31m✗${ESC}[0m test_churn — ${ESC}[33mexpected 2.1, got 2.4${ESC}[0m\n${ESC}[1;44;97m SUMMARY ${ESC}[0m 2 passed, ${ESC}[1;31m1 failed${ESC}[0m`

export const TRACEBACK = [
  `${ESC}[0;31m---------------------------------------------------------------------------${ESC}[0m`,
  `${ESC}[0;31mKeyError${ESC}[0m                                  Traceback (most recent call last)`,
  `Cell ${ESC}[0;32mIn[4], line 3${ESC}[0m\n${ESC}[1;32m      1${ESC}[0m df ${ESC}[38;5;241m=${ESC}[39m pd${ESC}[38;5;241m.${ESC}[39mread_csv(${ESC}[38;5;124m"orders.csv"${ESC}[39m)\n${ESC}[0;32m----> 3${ESC}[0m df[${ESC}[38;5;124m"revenue"${ESC}[39m]${ESC}[38;5;241m.${ESC}[39msum()`,
  `${ESC}[0;31mKeyError${ESC}[0m: 'revenue'`,
]

export const STREAM_STDOUT: JupyterOutput = { output_type: 'stream', name: 'stdout', text: 'Loading orders.csv …\nRead 12,408 rows in 0.21s\n' }
export const STREAM_STDERR: JupyterOutput = { output_type: 'stream', name: 'stderr', text: `${ESC}[33mUserWarning${ESC}[0m: column "signup_at" parsed as object, pass parse_dates=["signup_at"]\n` }
export const ERROR_OUT: JupyterOutput = { output_type: 'error', ename: 'KeyError', evalue: "'revenue'", traceback: TRACEBACK }
export const RESULT_PLAIN: JupyterOutput = { output_type: 'execute_result', execution_count: 5, data: { 'text/plain': 'region\nAPAC      288150.0\nEMEA      412880.0\nLATAM      96720.0\nNA       1102400.0\nName: mrr, dtype: float64' } }
export const RESULT_JSON: JupyterOutput = { output_type: 'execute_result', execution_count: 6, data: { 'application/json': JSON_DATA, 'text/plain': '{...}' } }
export const DISPLAY_PNG: JupyterOutput = { output_type: 'display_data', data: { 'image/png': PNG, 'text/plain': '<Figure size 320x160>' }, metadata: {} }
export const RESULT_HTML: JupyterOutput = { output_type: 'execute_result', execution_count: 7, data: { 'text/html': DATAFRAME_HTML, 'text/plain': '  region  accounts ...' } }
export const DISPLAY_MD: JupyterOutput = { output_type: 'display_data', data: { 'text/markdown': MARKDOWN } }
export const DISPLAY_SVG: JupyterOutput = { output_type: 'display_data', data: { 'image/svg+xml': SVG_CHART } }
export const DISPLAY_WIDGET: JupyterOutput = { output_type: 'display_data', data: { 'application/vnd.jupyter.widget-view+json': { model_id: 'int_slider', version_major: 2, version_minor: 0 }, 'text/plain': 'IntSlider(value=42)' } }

export const USERS = [
  { id: 'u1', name: 'Ada Lovelace', color: '#5250e9' },
  { id: 'u2', name: 'Grace Hopper', color: '#16a34a' },
  { id: 'u3', name: 'Katherine Johnson', color: '#d97706' },
  { id: 'u4', name: 'Alan Turing', color: '#0aa2dc' },
  { id: 'u5', name: 'Edsger Dijkstra', color: '#c82828' },
  { id: 'u6', name: 'Barbara Liskov', color: '#7c5fe6' },
  { id: 'u7', name: 'Margaret Hamilton', color: '#db2777' },
  { id: 'u8', name: 'Donald Knuth', color: '#475569' },
]

/** A long stdout stream (training log), for maxHeight / scrolling. */
export const LONG_STREAM: JupyterOutput = {
  output_type: 'stream', name: 'stdout',
  text: Array.from({ length: 120 }, (_, i) => `epoch ${String(i + 1).padStart(3)}/120  loss=${(1.9 * Math.exp(-i / 30) + 0.08).toFixed(4)}  val_acc=${(0.52 + 0.4 * (1 - Math.exp(-i / 25))).toFixed(3)}`).join('\n') + '\n',
}

/** An inline SVG portrait, so avatar images load without the network. */
export const avatarSrc = (initials: string, color: string) =>
  `data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="${color}"/><text x="32" y="41" font-family="sans-serif" font-size="26" font-weight="700" fill="#fff" text-anchor="middle">${initials}</text></svg>`)}`

export const CODE = {
  python: `import pandas as pd

def churn_rate(df: pd.DataFrame, window: int = 30) -> float:
    """Share of accounts that cancelled in the last \`window\` days."""
    recent = df[df.cancelled_at >= pd.Timestamp.now() - pd.Timedelta(days=window)]
    return round(len(recent) / len(df), 4)

print(f"churn: {churn_rate(accounts):.2%}")`,
  ipython: `%%time
!pip install -q polars
%matplotlib inline
accounts = %sql SELECT * FROM accounts LIMIT 100
accounts?`,
  sql: `-- monthly recurring revenue by region
SELECT region,
       date_trunc('month', billed_at) AS month,
       sum(amount) FILTER (WHERE status = 'paid') AS mrr
FROM invoices
WHERE billed_at >= now() - interval '1 year'
GROUP BY 1, 2
ORDER BY 2 DESC;`,
  markdown: `# Revenue review

Numbers are **refreshed nightly** from \`invoices\`.

- [x] NA and EMEA
- [ ] APAC pricing

> Churn is the share of accounts that cancelled.`,
  json: `{
  "dataset": "accounts",
  "version": 7,
  "schema": [{ "name": "mrr", "type": "float64" }],
  "sampled": false
}`,
  javascript: `export function render({ model, el }) {
  const btn = document.createElement("button");
  btn.onclick = () => { model.set("value", model.get("value") + 1); model.save_changes(); };
  el.append(btn);
}`,
  typescript: `type Row = { region: string; mrr: number };
const total = (rows: Row[]): number => rows.reduce((s, r) => s + r.mrr, 0);`,
  html: `<section class="card">
  <h2>Quarterly summary</h2>
  <p>Revenue grew <strong>12%</strong>.</p>
</section>`,
  plain: `orders.csv  12,408 rows
accounts    4,403 rows
refreshed   2026-10-31 08:45 UTC`,
} as const
