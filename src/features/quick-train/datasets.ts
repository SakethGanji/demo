// Sample datasets, the in-browser CSV profiler (mock mode), and the adapter for profiles from the API (live mode).

import type { DatasetDetail, Profile } from '@/shared/api/types'

export type Col = { name: string; type: 'text' | 'category' | 'number' | 'id' | 'date'; sample: string; missing?: number }

export type Dataset = {
  name: string; rows: number; cols: Col[]; target: string; classes: { name: string; share: number }[]; kind: 'text' | 'tabular'; note: string
  /** Set when the dataset lives on the API; absent for the built-in mock samples and browser-profiled files. */
  id?: string
  sample?: boolean
  profile?: Profile
}

/** A dataset profiled by the API, in the shape the pages already use. */
export function fromServer(d: DatasetDetail): Dataset {
  const p = d.profile
  return {
    id: d.id, sample: d.sample, profile: p, name: d.filename, rows: p.rows, kind: p.kind, note: d.note,
    target: p.target ?? '',
    cols: p.columns.map((c) => ({ name: c.name, type: c.type, sample: c.example.slice(0, 48), missing: c.missing })),
    classes: p.classes.map((c) => ({ name: c.name, share: c.share })),
  }
}

export const SAMPLES: Dataset[] = [
  {
    name: 'zendesk_tickets_2026_q3.parquet', rows: 124500, kind: 'text', target: 'category', note: 'Support tickets · Text + Metadata',
    cols: [
      { name: 'ticket_id', type: 'id', sample: 'ZD-481920' }, { name: 'ticket_body', type: 'text', sample: 'I was charged twice for my March invoice and…' },
      { name: 'subject', type: 'text', sample: 'Double charge on invoice' }, { name: 'priority', type: 'category', sample: 'high' },
      { name: 'channel', type: 'category', sample: 'email' }, { name: 'customer_tier', type: 'category', sample: 'enterprise' }, { name: 'language', type: 'category', sample: 'en' }, { name: 'created_at', type: 'number', sample: '2026-08-14T09:21' }, { name: 'category', type: 'category', sample: 'Billing' },
    ],
    classes: [['Billing', 0.175], ['Technical', 0.215], ['Account', 0.139], ['Security', 0.072], ['Shipping', 0.101], ['Other', 0.297]].map(([name, share]) => ({ name: name as string, share: share as number })),
  },
  {
    name: 'product_reviews_2026.csv', rows: 58200, kind: 'text', target: 'sentiment', note: 'Product reviews · Text',
    cols: [
      { name: 'review_id', type: 'id', sample: 'R-99214' }, { name: 'review_text', type: 'text', sample: 'Battery lasts two days, but the strap broke in…' },
      { name: 'stars', type: 'number', sample: '3' }, { name: 'product_line', type: 'category', sample: 'wearables' }, { name: 'sentiment', type: 'category', sample: 'Mixed' },
    ],
    classes: [['Positive', 0.58], ['Mixed', 0.17], ['Negative', 0.25]].map(([name, share]) => ({ name: name as string, share: share as number })),
  },
  {
    name: 'account_churn_2026.csv', rows: 41870, kind: 'tabular', target: 'churned', note: 'Accounts · Numeric + Categorical',
    cols: [
      { name: 'account_id', type: 'id', sample: 'A-20481' }, { name: 'seats', type: 'number', sample: '42' }, { name: 'mrr_usd', type: 'number', sample: '3,180.00' },
      { name: 'tickets_90d', type: 'number', sample: '7' }, { name: 'plan', type: 'category', sample: 'business' }, { name: 'region', type: 'category', sample: 'emea' },
      { name: 'last_login_days', type: 'number', sample: '19' }, { name: 'churned', type: 'category', sample: 'No' },
    ],
    classes: [['No', 0.86], ['Yes', 0.14]].map(([name, share]) => ({ name: name as string, share: share as number })),
  },
]

export async function profileCsv(file: File): Promise<Dataset> {
  const text = await file.slice(0, 256_000).text()
  const split = (l: string) => l.match(/("([^"]|"")*"|[^,]*)(,|$)/g)?.map((c) => c.replace(/,$/, '').replace(/^"|"$/g, '').trim()).slice(0, -1) ?? []
  const lines = text.split(/\r?\n/).filter(Boolean)
  if (lines.length < 3) throw new Error('This file has fewer than two data rows.')
  const head = split(lines[0]); const rows = lines.slice(1, 400).map(split)
  if (head.length < 2) throw new Error('No comma-separated columns found. Give a CSV with a header row.')
  const cols: Col[] = head.map((name, i) => {
    const vals = rows.map((r) => r[i] ?? '').filter(Boolean)
    const uniq = new Set(vals).size
    const numeric = vals.length > 0 && vals.every((v) => !Number.isNaN(Number(v.replace(/,/g, ''))))
    const avg = vals.reduce((a, v) => a + v.length, 0) / (vals.length || 1)
    const type: Col['type'] = numeric ? 'number' : avg > 40 ? 'text' : uniq > vals.length * 0.9 && vals.length > 20 ? 'id' : 'category'
    return { name: name || `col_${i + 1}`, type, sample: (vals[0] ?? '').slice(0, 48) }
  })
  const cats = cols.filter((c) => c.type === 'category')
  const target = (cols.find((c) => /label|target|categor|class|sentiment|churn|^y$/i.test(c.name) && c.type !== 'text') ?? cats[cats.length - 1] ?? cols[cols.length - 1]).name
  const ti = cols.findIndex((c) => c.name === target)
  const counts = new Map<string, number>()
  rows.forEach((r) => { const v = r[ti]; if (v) counts.set(v, (counts.get(v) ?? 0) + 1) })
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  const tot = top.reduce((a, [, n]) => a + n, 0) || 1
  const bytesPerRow = text.length / lines.length
  return {
    name: file.name, rows: Math.max(rows.length, Math.round(file.size / bytesPerRow) - 1), cols, target,
    classes: top.map(([name, n]) => ({ name: name.slice(0, 18), share: n / tot })), kind: cols.some((c) => c.type === 'text') ? 'text' : 'tabular', note: 'Your file · profiled from the first rows',
  }
}
