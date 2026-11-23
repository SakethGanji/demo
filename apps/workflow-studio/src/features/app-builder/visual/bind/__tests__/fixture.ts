/** A small generated-style app for the wiring tests: a client, a resource, a polled screen and a state-loaded screen. */
import { indexElements, parseSource, type FileMap } from '../../source'
import { locateCtx, type Built, type Ctx } from '../scope'

export const CLIENT = `async function call<T>(method: string, path: string, opts: { query?: Record<string, unknown>; body?: unknown } = {}): Promise<T> {
  return fetch(path, { method }).then((r) => r.json())
}

export type ListRunsResponse = { rows: { id: string; status: string; started_at: string }[]; total: number }
export type ListExpensesResponse = any

export const api = {
  /** GET /runs — list runs */
  listRuns: (query?: Record<string, unknown>): Promise<ListRunsResponse> => call<ListRunsResponse>('GET', \`/runs\`, { query }),
  /** GET /expenses — Returns {items: [{id, title, amount}], total} */
  listExpenses: (query?: Record<string, unknown>): Promise<ListExpensesResponse> => call<ListExpensesResponse>('GET', \`/expenses\`, { query }),
  /** POST /runs/{id}/cancel — cancel a run */
  cancelRun: (params: { id: string | number }, body?: unknown, query?: Record<string, unknown>): Promise<any> => call<any>('POST', \`/runs/\${encodeURIComponent(String(params.id))}/cancel\`, { query, body }),
}
`

export const RESOURCES = `import type { ResourceDef } from '../kit'

export const expenses: ResourceDef<any> = {
  name: 'expenses',
  label: 'Expenses',
  singular: 'Expense',
  titleField: 'title',
  fields: [{ name: 'title' }, { name: 'amount' }, { name: 'status' }],
  client: null as any,
}

export const vendors: ResourceDef<any> = {
  name: 'vendors',
  label: 'Vendors',
  singular: 'Vendor',
  titleField: 'name',
  fields: [{ name: 'name' }, { name: 'country' }],
  client: null as any,
}
`

export const RUNS = `import { useState } from 'react'
import { DataTable, Stat, Button, usePoll } from '../kit'
import { api } from '../api/client'

export interface Props { go: (screen: string) => void }

export default function Runs({ go }: Props) {
  const runs = usePoll(() => api.listRuns(), 5000)
  const [open, setOpen] = useState(false)
  return (
    <div>
      <Stat label="Runs" value={runs.data?.total ?? 0} />
      <DataTable rows={runs.data?.rows ?? []} columns={[{ key: 'id', header: 'ID' }]} />
      <Button onClick={() => setOpen(true)}>New</Button>
      <Button onClick={async () => { await api.cancelRun({ id: 1 }); runs.refresh() }}>Cancel</Button>
      <Button onClick={() => { doThing(); go('home') }}>Custom</Button>
      <Button>Plain</Button>
      {open && <p>Panel</p>}
      <p>Hello</p>
    </div>
  )
}
`

export const EXPENSES = `import { useCallback, useEffect, useState } from 'react'
import { DataTable, ListDetailLayout } from '../kit'
import { api } from '../api/client'

interface Expense { id: string; title: string; amount: number; owner: string }

export default function Expenses() {
  const [rows, setRows] = useState<Expense[]>([])
  const [reloadKey, setReloadKey] = useState(0)
  const reload = useCallback(() => setReloadKey((k) => k + 1), [])
  useEffect(() => {
    api.listExpenses().then((res) => setRows(res.items))
  }, [reloadKey])
  return (
    <div>
      <DataTable rows={rows} columns={[{ key: 'title', header: 'Title' }]} />
      <DataTable rows={rows.length ? rows : []} columns={[]} />
      <ListDetailLayout resource="expenses" title="Expenses" columns={['title', 'amount']} />
      <button onClick={reload}>Refresh</button>
    </div>
  )
}

function Inner({ items }: { items: Expense[] }) {
  return (
    <section>
      <DataTable rows={items} columns={[]} />
    </section>
  )
}
`

export const APP = `import { AppShell } from './kit'
import Runs from './views/Runs'
import Expenses from './views/Expenses'

export default function App() {
  return (
    <AppShell
      title="Demo"
      screens={[
        { id: 'runs', label: 'Runs', render: (go) => <Runs go={go} /> },
        { id: 'expenses', label: 'Expenses', roles: ['approver'], render: () => <Expenses /> },
      ]}
    />
  )
}
`

export const FILES: FileMap = {
  'api/client.ts': CLIENT,
  'api/resources.ts': RESOURCES,
  'views/Runs.tsx': RUNS,
  'views/Expenses.tsx': EXPENSES,
  'App.tsx': APP,
}

/** The wiring context of the nth `<name>` element in a file (`text` narrows to an element whose source contains it). */
export function ctxFor(files: FileMap, file: string, name: string, text?: string): Ctx {
  const src = files[file]
  const hit = indexElements(parseSource(src)).find((e) => e.name === name && (!text || src.slice(e.node.start!, e.node.end!).includes(text)))
  if (!hit) throw new Error(`no <${name}> in ${file}`)
  const ctx = locateCtx(files, { file, key: hit.key })
  if (!ctx) throw new Error('no context')
  return ctx
}

/** The patched source of a successful build (fails the test otherwise). */
export function after(b: Built): string {
  if (!b.ok) throw new Error(`refused: ${b.error}`)
  return b.patches[0].after!
}
