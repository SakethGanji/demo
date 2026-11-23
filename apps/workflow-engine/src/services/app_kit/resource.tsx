/** Studio UI kit — resource library (provided by Studio, refreshed every builder turn; do not edit).
 * Backend-bound blocks and page layouts for `studio.resource` routes; each fetches for itself and handles its own loading,
 * empty and error states. Take `resource` (a ResourceDef or its registered name) plus a few props. Colours come from the
 * CSS variables of the app's index.css (--primary, --card, --border, --radius, ...). */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { ColorSchemeToggle } from './layout'
import { cx } from './ui'

// ── Types ────────────────────────────────────────────────────────────────────

export type BaseRow = { id: number; created_at?: string; updated_at?: string }
export type Page<T> = { rows: T[]; total: number; limit: number; offset: number }
/** Query of a list route: `limit`, `offset`, `sort` (`-col` descending), `q` (search) and exact filters on filter columns. */
export type ListQuery = { limit?: number; offset?: number; sort?: string; q?: string; include_archived?: boolean } & Record<string, string | number | boolean | undefined>
export type Filters = Record<string, string | number | boolean>
export type FieldType = 'string' | 'text' | 'integer' | 'number' | 'boolean' | 'datetime'

export interface ResourceClient<Row extends BaseRow> {
  list(q?: ListQuery): Promise<Page<Row>>
  get?(id: number): Promise<Row>
  create?(body: Record<string, unknown>): Promise<Row>
  update?(id: number, body: Record<string, unknown>): Promise<Row>
  remove?(id: number): Promise<unknown>
  transition?(id: number, to: string, body?: Record<string, unknown>): Promise<Row>
}

/** Where a reference column points: the other resource's client and the field to show as its label. */
export interface RefTarget { client: { list(q?: ListQuery): Promise<Page<{ id: number }>> }; labelField: string }

export interface FieldDef {
  name: string
  label: string
  type: FieldType
  required?: boolean
  enum?: string[]
  ref?: RefTarget
  /** shown but never edited (ids, timestamps, state, audit columns) */
  readOnly?: boolean
  /** left out of tables and forms (still shown in the record panel) */
  hidden?: boolean
  help?: string
  /** offer a filter control in the table (default: enum and boolean fields) */
  filter?: boolean
}

export interface Transition {
  from: string
  to: string
  label?: string
  tone?: 'primary' | 'danger' | 'default'
  /** ask for a comment before moving; `required` blocks an empty one */
  comment?: 'optional' | 'required'
  /** body field the comment is sent as (default `comment`) */
  commentField?: string
}

export interface StateMachine { field: string; initial?: string; final?: string[]; transitions: Transition[] }

export interface ResourceDef<Row extends BaseRow> {
  name: string
  label: string
  singular: string
  /** field used as the record's title */
  titleField: string
  fields: FieldDef[]
  client: ResourceClient<Row>
  states?: StateMachine
  /** the list route supports `q` */
  search?: boolean
}

/** HTML attributes a block passes to its root element (`id`, `style`, `data-*`, `aria-*`), minus names the kit uses itself
 * (`resource` is an RDFa attribute on plain elements). */
type Attrs<E = HTMLDivElement> = Omit<React.HTMLAttributes<E>, 'title' | 'children' | 'color' | 'content' | 'onChange' | 'onSelect' | 'resource' | 'defaultValue' | 'placeholder' | 'prefix'>

export type ErrorInfo = { code: string; message: string; field?: string }

/** The `{error, message, field?}` body of a failed route, or the plain error message. */
export function errorInfo(e: unknown): ErrorInfo {
  const body = typeof e === 'object' && e !== null && 'body' in e ? (e as { body: unknown }).body : null
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>
    if (typeof b.message === 'string') return { code: String(b.error ?? 'error'), message: b.message, field: typeof b.field === 'string' ? b.field : undefined }
  }
  return { code: 'error', message: e instanceof Error ? e.message : 'Something went wrong' }
}

export function val(row: object, key: string): unknown {
  return (row as Record<string, unknown>)[key]
}

export function titleCase(s: string): string {
  const t = s.replace(/[_-]+/g, ' ').trim()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

function show(f: FieldDef, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (f.type === 'boolean') return v ? 'Yes' : 'No'
  if (f.type === 'datetime' && typeof v === 'string') {
    const d = new Date(v)
    return Number.isNaN(d.getTime()) ? v : d.toLocaleString()
  }
  if (f.type === 'number' && typeof v === 'number') return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return String(v)
}

// ── Look (class names are literal so Tailwind sees them; colours come from index.css) ──────────────────────────────

const K = {
  card: 'min-w-0 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]',
  muted: 'text-[var(--muted-foreground)]',
  input: 'w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-sm text-[var(--foreground)] outline-none focus:border-[var(--primary)] disabled:opacity-50',
  btn: 'inline-flex items-center justify-center gap-1.5 rounded-[var(--radius)] border px-3 py-1.5 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-50 hover:opacity-90',
  primary: 'border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]',
  default: 'border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]',
  danger: 'border-[var(--danger)] bg-[var(--card)] text-[var(--danger)]',
  th: 'px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]',
  td: 'px-3 py-2 text-sm',
}

function Btn({ tone = 'default', small, loading, className = '', children, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'primary' | 'danger' | 'default'; small?: boolean; loading?: boolean }) {
  return (
    <button type="button" {...rest} disabled={rest.disabled || loading}
      className={className ? cx(`${K.btn} ${K[tone]} ${small ? 'px-2 py-1 text-xs' : ''}`, className) : `${K.btn} ${K[tone]} ${small ? 'px-2 py-1 text-xs' : ''}`}>
      {loading ? '…' : null}{children}
    </button>
  )
}

const BADGE: Record<string, string> = {
  good: 'bg-[var(--success-soft)] text-[var(--success)]',
  warn: 'bg-[var(--warning-soft)] text-[var(--warning)]',
  bad: 'bg-[var(--danger-soft)] text-[var(--danger)]',
  none: 'bg-[var(--muted)] text-[var(--muted-foreground)]',
}

function toneOf(state: string): keyof typeof BADGE {
  const s = state.toLowerCase()
  if (/(approv|paid|done|complete|success|accept|active|resolved|closed|shipped|deliver|fulfil)/.test(s)) return 'good'
  if (/(reject|fail|error|cancel|declin|denied|overdue|blocked)/.test(s)) return 'bad'
  if (/(pending|review|submitted|waiting|open|progress|queued|new)/.test(s)) return 'warn'
  return 'none'
}

/** A state as a coloured pill.
 * @category display
 * @example <StateBadge value="approved" /> */
export function StateBadge({ value, className, ...rest }: { value: unknown; className?: string } & Attrs<HTMLSpanElement>) {
  const s = value === null || value === undefined || value === '' ? '—' : String(value)
  return <span data-vcomp="StateBadge" {...rest} className={cx('inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium', BADGE[toneOf(s)], className)}>{titleCase(s)}</span>
}

function Notice({ kind, children }: { kind: 'error' | 'ok'; children: React.ReactNode }) {
  return (
    <div role={kind === 'error' ? 'alert' : 'status'}
      className={`rounded-[var(--radius)] px-3 py-2 text-sm ${kind === 'error' ? BADGE.bad : BADGE.good}`}>{children}</div>
  )
}

// ── Registry ─────────────────────────────────────────────────────────────────

/** A ResourceDef or the name it was registered under (`registerResources` in api/resources.ts). */
export type Res<Row extends BaseRow> = ResourceDef<Row> | string
const registry: Record<string, ResourceDef<BaseRow>> = {}

/** Register the app's resources by name so blocks and layouts can be given `resource="supplyRequests"`. */
export function registerResources(defs: Record<string, ResourceDef<BaseRow>>): void {
  for (const [k, d] of Object.entries(defs)) registry[k] = d as unknown as ResourceDef<BaseRow>
}

function resolve<Row extends BaseRow>(r: Res<Row>): ResourceDef<Row> {
  if (typeof r !== 'string') return r
  const d = registry[r] ?? Object.values(registry).find((x) => x.name === r)
  if (!d) throw new Error(`Unknown resource "${r}" — register it with registerResources`)
  return d as unknown as ResourceDef<Row>
}

// ── Toasts ───────────────────────────────────────────────────────────────────

type ToastItem = { id: number; kind: 'ok' | 'error'; text: string }
let toastSeq = 0
const toastListeners = new Set<(t: ToastItem) => void>()

/** Show a short message; needs one `<ToastHost />` mounted in the app. */
export function notify(text: string, kind: 'ok' | 'error' = 'ok'): void {
  const t = { id: ++toastSeq, kind, text }
  toastListeners.forEach((l) => l(t))
}

/** Mount once (App.tsx).
 * @category overlay
 * @example <ToastHost /> */
export function ToastHost({ className, ...rest }: { className?: string } & Attrs = {}) {
  const [items, setItems] = useState<ToastItem[]>([])
  useEffect(() => {
    const on = (t: ToastItem) => {
      setItems((cur) => [...cur, t])
      setTimeout(() => setItems((cur) => cur.filter((x) => x.id !== t.id)), 3500)
    }
    toastListeners.add(on)
    return () => { toastListeners.delete(on) }
  }, [])
  return (
    <div data-vcomp="ToastHost" {...rest} className={cx('pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2', className)}>
      {items.map((t) => <div key={t.id} className="pointer-events-auto"><Notice kind={t.kind}>{t.text}</Notice></div>)}
    </div>
  )
}

// ── Visual-editor hooks ──────────────────────────────────────────────────────

/** First index of each name in a list prop (`columns`, `fields`): the `data-vpart` index of the part it renders. */
function partIndex(names?: string[]): Map<string, number> | null {
  if (!names) return null
  const m = new Map<string, number>()
  names.forEach((n, i) => { if (!m.has(n)) m.set(n, i) })
  return m
}

/** The prop name ResourceTable's column parts are tagged with (`recentColumns` inside DashboardLayout). */
const ColumnsPart = createContext('columns')

/** useState that, inside the Studio preview (`globalThis.__studioPreviewState`), survives a re-render of the app under `key`. */
function usePreviewState(key: string, init: string, valid: (v: string) => boolean): [string, (v: string) => void] {
  const [v, setV] = useState<string>(() => {
    const st = (globalThis as any).__studioPreviewState
    const saved = st && typeof st === 'object' && !Array.isArray(st) ? st[key] : undefined
    return typeof saved === 'string' && valid(saved) ? saved : init
  })
  const set = useCallback((x: string) => {
    setV(x)
    const st = (globalThis as any).__studioPreviewState
    if (st && typeof st === 'object' && !Array.isArray(st)) st[key] = x
  }, [key])
  return [v, set]
}

// ── Page chrome ──────────────────────────────────────────────────────────────

/** Title, one-line purpose and right-aligned actions at the top of a page.
 * @category page
 * @example <PageHeader title="Expenses" subtitle="Claims waiting for approval" actions={<Button variant="primary">New</Button>} /> */
export function PageHeader({ title, subtitle, actions, className, ...rest }: { /** @control text */ title: React.ReactNode; /** @control text */ subtitle?: React.ReactNode; actions?: React.ReactNode; className?: string } & Omit<React.HTMLAttributes<HTMLDivElement>, 'title' | 'children'>) {
  return (
    <div data-vcomp="PageHeader" {...rest} className={cx('mb-4 flex flex-wrap items-start gap-3 text-[var(--foreground)]', className)}>
      <div className="min-w-0 flex-1">
        <h1 className="m-0 text-xl font-semibold">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm opacity-65">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

/** Bordered panel with an optional title.
 * @category layout
 * @example <Panel title="Details">Content</Panel> */
export function Panel({ title, actions, children, className, headerClassName, bodyClassName, ...rest }: { /** @control text */ title?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode; className?: string; headerClassName?: string; bodyClassName?: string } & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <section data-vcomp="Panel" {...rest} className={cx(K.card, className)}>
      {title || actions ? (
        <header data-vpart="header" className={cx('flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-3', headerClassName)}>
          {title ? <h2 className="m-0 text-sm font-semibold">{title}</h2> : null}
          {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div data-vpart="body" className={cx('p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

// ── Data hook ────────────────────────────────────────────────────────────────

/** Load one page of a resource; `reload()` fetches again. A new query (by value) refetches. */
export function useResourceList<Row extends BaseRow>(resource: Res<Row>, query: ListQuery = {}) {
  const def = resolve(resource)
  const [state, setState] = useState<{ data: Page<Row> | null; error: ErrorInfo | null; loading: boolean }>({ data: null, error: null, loading: true })
  const [nonce, setNonce] = useState(0)
  const key = JSON.stringify(query)
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true }))
    const q = JSON.parse(key) as ListQuery
    delete q._r
    def.client.list(q).then(
      (data) => { if (alive) setState({ data, error: null, loading: false }) },
      (e: unknown) => { if (alive) setState({ data: null, error: errorInfo(e), loading: false }) },
    )
    return () => { alive = false }
  }, [def, key, nonce])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { ...state, reload }
}

// ── ResourceTable ────────────────────────────────────────────────────────────

/** Backend-bound list of a resource's records (sorted, paged, searchable; fetches by itself). `columns` picks fields; `filters`
 * fixes a query such as `{ status: 'pending' }`. For plain in-memory rows use DataTable.
 * @category data
 * @example <ResourceTable resource="expenses" columns={['title', 'amount', 'status']} filters={{ status: 'pending' }} /> */
export function ResourceTable<Row extends BaseRow>({ resource, columns, filters, pageSize = 20, onSelect, selectedId, onCreate, createLabel, emptyText, actions, rowActions, reloadKey = 0,
  density = 'comfortable', className, toolbarClassName, footerClassName, ...rest }: Attrs & {
  resource: Res<Row>
  columns?: string[]
  filters?: Filters
  pageSize?: number
  onSelect?: (row: Row) => void
  selectedId?: number | null
  onCreate?: () => void
  createLabel?: string
  /** @control text */
  emptyText?: React.ReactNode
  actions?: React.ReactNode
  rowActions?: (row: Row) => React.ReactNode
  /** change it to refetch (after a save elsewhere on the page) */
  reloadKey?: number
  /** Row height.
   * @control select @options comfortable|compact @group Style @default comfortable */
  density?: 'comfortable' | 'compact'
  className?: string
  toolbarClassName?: string
  footerClassName?: string
}) {
  const def = resolve(resource)
  const td = density === 'compact' ? `${K.td} py-1` : K.td
  const part = useContext(ColumnsPart)
  const parts = partIndex(columns)
  const vpart = (name: string) => (parts?.has(name) ? `${part}.${parts.get(name)}` : undefined)
  const cols = useMemo(() => {
    const visible = def.fields.filter((f) => !f.hidden)
    return columns ? columns.map((n) => visible.find((f) => f.name === n)).filter((f): f is FieldDef => !!f) : visible.slice(0, 6)
  }, [def, columns])
  const filterFields = def.fields.filter((f) => !f.hidden && (f.filter ?? (!!f.enum || f.type === 'boolean')) && !(filters && f.name in filters)).slice(0, 3)
  const [sort, setSort] = useState<string>('')
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [chosen, setChosen] = useState<Record<string, string>>({})
  useEffect(() => {
    const t = setTimeout(() => { setQ(search); setOffset(0) }, 250)
    return () => clearTimeout(t)
  }, [search])
  const query: ListQuery = { limit: pageSize, offset, ...(sort ? { sort } : {}), ...(q ? { q } : {}), ...filters }
  for (const [k, v] of Object.entries(chosen)) if (v !== '') query[k] = v
  const { data, error, loading, reload } = useResourceList(def, { ...query, _r: reloadKey })
  const total = data?.total ?? 0
  const sortBy = (name: string) => { setSort((s) => (s === name ? `-${name}` : name)); setOffset(0) }
  const cell = (f: FieldDef, row: Row) => {
    const v = val(row, f.name)
    if (def.states && def.states.field === f.name) return <StateBadge value={v} />
    return f.name === def.titleField ? <span className="font-medium">{show(f, v)}</span> : show(f, v)
  }
  return (
    <div data-vcomp="ResourceTable" {...rest} className={cx(K.card, className)}>
      <div data-vpart="toolbar" className={cx('flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-2', toolbarClassName)}>
        {def.search ? <input aria-label={`Search ${def.label}`} className={`${K.input} max-w-xs`} placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} /> : null}
        {filterFields.map((f) => (
          <select key={f.name} aria-label={`Filter by ${f.label}`} className={`${K.input} w-auto`} value={chosen[f.name] ?? ''}
            onChange={(e) => { setChosen((c) => ({ ...c, [f.name]: e.target.value })); setOffset(0) }}>
            <option value="">{f.label}: all</option>
            {(f.enum ?? ['true', 'false']).map((o) => <option key={o} value={o}>{f.type === 'boolean' ? (o === 'true' ? 'Yes' : 'No') : titleCase(o)}</option>)}
          </select>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {actions}
          {onCreate ? <Btn tone="primary" onClick={onCreate}>{createLabel ?? `New ${def.singular.toLowerCase()}`}</Btn> : null}
        </div>
      </div>
      {error ? <div className="p-3"><Notice kind="error">{error.message} <button type="button" className="underline" onClick={reload}>Retry</button></Notice></div> : null}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-[var(--border)]">
              {cols.map((f) => (
                <th key={f.name} data-vpart={vpart(f.name)} className={K.th} aria-sort={sort === f.name ? 'ascending' : sort === `-${f.name}` ? 'descending' : 'none'}>
                  <button type="button" className="uppercase" onClick={() => sortBy(f.name)}>{f.label}{sort === f.name ? ' ↑' : sort === `-${f.name}` ? ' ↓' : ''}</button>
                </th>
              ))}
              {rowActions ? <th className={K.th} /> : null}
            </tr>
          </thead>
          <tbody>
            {data?.rows.map((row) => (
              <tr key={row.id} onClick={onSelect ? () => onSelect(row) : undefined}
                className={`border-b border-[var(--border)] last:border-0 ${onSelect ? 'cursor-pointer hover:bg-[var(--muted)]' : ''} ${selectedId === row.id ? 'bg-[var(--muted)]' : ''}`}>
                {cols.map((f) => <td key={f.name} data-vpart={vpart(f.name)} className={td}>{cell(f, row)}</td>)}
                {rowActions ? <td className={`${td} text-right`} onClick={(e) => e.stopPropagation()}>{rowActions(row)}</td> : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loading && !data ? <p className={`px-4 py-8 text-center text-sm ${K.muted}`}>Loading…</p> : null}
      {!loading && !error && data && data.rows.length === 0 ? (
        <p className={`px-4 py-8 text-center text-sm ${K.muted}`}>{emptyText ?? `No ${def.label.toLowerCase()} yet.`}</p>
      ) : null}
      <div data-vpart="footer" className={cx(`flex items-center gap-2 px-3 py-2 text-xs ${K.muted}`, footerClassName)}>
        <span>{total === 0 ? '0' : `${offset + 1}–${Math.min(offset + pageSize, total)}`} of {total}</span>
        <div className="ml-auto flex gap-2">
          <Btn small disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous</Btn>
          <Btn small disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)}>Next</Btn>
        </div>
      </div>
    </div>
  )
}

// ── ResourceForm ─────────────────────────────────────────────────────────────

function RefSelect({ field, value, onChange, id }: { field: FieldDef; value: string; onChange: (v: string) => void; id: string }) {
  const [options, setOptions] = useState<Array<{ id: number; label: string }>>([])
  useEffect(() => {
    let alive = true
    field.ref?.client.list({ limit: 1000 }).then((p) => {
      if (alive) setOptions(p.rows.map((r) => ({ id: r.id, label: String(val(r, field.ref?.labelField ?? 'id') ?? r.id) })))
    }, () => undefined)
    return () => { alive = false }
  }, [field])
  return (
    <select id={id} className={K.input} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select…</option>
      {options.map((o) => <option key={o.id} value={String(o.id)}>{o.label}</option>)}
    </select>
  )
}

function editable(f: FieldDef, stateField?: string): boolean {
  return !f.readOnly && !f.hidden && f.name !== stateField
}

/** Create or edit form generated from the def: input per field type, enum selects, reference pickers, required checks and the
 * route's `{error, message, field}` errors shown on the field. Pass `record` to edit; `fields` limits/orders the fields.
 * @category form
 * @example <ResourceForm resource="expenses" fields={['title', 'amount']} submitLabel="Submit" onSaved={() => go('list')} /> */
export function ResourceForm<Row extends BaseRow>({ resource, record, initial, fields, submitLabel, onSaved, onCancel, className, ...rest }: Omit<Attrs<HTMLFormElement>, 'onSubmit'> & {
  resource: Res<Row>
  record?: Row | null
  initial?: Record<string, unknown>
  fields?: string[]
  submitLabel?: string
  onSaved?: (row: Row) => void
  onCancel?: () => void
  className?: string
}) {
  const def = resolve(resource)
  const list = useMemo(() => {
    const all = def.fields.filter((f) => editable(f, def.states?.field))
    return fields ? fields.map((n) => all.find((f) => f.name === n)).filter((f): f is FieldDef => !!f) : all
  }, [def, fields])
  const parts = partIndex(fields)
  const start = useCallback((): Record<string, string> => {
    const out: Record<string, string> = {}
    for (const f of list) {
      const v = record ? val(record, f.name) : initial?.[f.name]
      out[f.name] = v === null || v === undefined ? (f.type === 'boolean' ? 'false' : '') : String(v)
    }
    return out
  }, [list, record, initial])
  const [values, setValues] = useState<Record<string, string>>(start)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [banner, setBanner] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const recordId = record?.id
  useEffect(() => { setValues(start()); setErrors({}); setBanner(null) }, [recordId]) // eslint-disable-line react-hooks/exhaustive-deps
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const found: Record<string, string> = {}
    const body: Record<string, unknown> = {}
    for (const f of list) {
      const raw = (values[f.name] ?? '').trim()
      if (raw === '') {
        if (f.required) found[f.name] = `${f.label} is required`
        else if (record) body[f.name] = null
        continue
      }
      if (f.type === 'integer' || f.type === 'number' || f.ref) {
        const n = Number(raw)
        if (Number.isNaN(n) || (f.type === 'integer' && !Number.isInteger(n))) { found[f.name] = `${f.label} must be a number`; continue }
        body[f.name] = n
      } else if (f.type === 'boolean') body[f.name] = raw === 'true'
      else if (f.type === 'datetime') body[f.name] = raw.length === 16 ? `${raw}:00` : raw
      else body[f.name] = raw
    }
    setErrors(found)
    if (Object.keys(found).length) return
    setBusy(true)
    setBanner(null)
    try {
      const saved = record
        ? await (def.client.update ? def.client.update(record.id, body) : Promise.reject(new Error('This record cannot be edited')))
        : await (def.client.create ? def.client.create(body) : Promise.reject(new Error('This record cannot be created')))
      notify(`${def.singular} ${record ? 'saved' : 'created'}`)
      if (!record) setValues(start())
      onSaved?.(saved)
    } catch (err) {
      const info = errorInfo(err)
      if (info.field && list.some((f) => f.name === info.field)) setErrors({ [info.field]: info.message })
      else setBanner(info.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form data-vcomp="ResourceForm" {...rest} onSubmit={submit} className={cx('flex flex-col gap-3', className)} noValidate>
      {banner ? <Notice kind="error">{banner}</Notice> : null}
      {list.map((f) => {
        const id = `${def.name}-${f.name}`
        const v = values[f.name] ?? ''
        const set = (x: string) => setValues((cur) => ({ ...cur, [f.name]: x }))
        return (
          <div key={f.name} className="flex flex-col gap-1" data-vpart={parts?.has(f.name) ? `fields.${parts.get(f.name)}` : undefined}>
            {f.type === 'boolean' ? (
              <label className="flex items-center gap-2 text-sm"><input id={id} type="checkbox" checked={v === 'true'} onChange={(e) => set(e.target.checked ? 'true' : 'false')} />{f.label}</label>
            ) : (
              <label htmlFor={id} className="text-sm font-medium">{f.label}{f.required ? <span className="text-[var(--danger)]"> *</span> : null}</label>
            )}
            {f.ref ? <RefSelect id={id} field={f} value={v} onChange={set} />
              : f.enum ? (
                <select id={id} className={K.input} value={v} onChange={(e) => set(e.target.value)}>
                  <option value="">Select…</option>
                  {f.enum.map((o) => <option key={o} value={o}>{titleCase(o)}</option>)}
                </select>
              ) : f.type === 'text' ? <textarea id={id} rows={3} className={K.input} value={v} onChange={(e) => set(e.target.value)} />
              : f.type === 'boolean' ? null
              : <input id={id} className={K.input} value={v} onChange={(e) => set(e.target.value)}
                  type={f.type === 'integer' || f.type === 'number' ? 'number' : f.type === 'datetime' ? 'datetime-local' : 'text'}
                  step={f.type === 'number' ? 'any' : undefined} />}
            {f.help && !errors[f.name] ? <span className={`text-xs ${K.muted}`}>{f.help}</span> : null}
            {errors[f.name] ? <span role="alert" className="text-xs text-[var(--danger)]">{errors[f.name]}</span> : null}
          </div>
        )
      })}
      <div data-vpart="footer" className="flex gap-2">
        <Btn tone="primary" type="submit" loading={busy}>{submitLabel ?? (record ? 'Save changes' : `Create ${def.singular.toLowerCase()}`)}</Btn>
        {onCancel ? <Btn onClick={onCancel}>Cancel</Btn> : null}
      </div>
    </form>
  )
}

// ── StatusActions ────────────────────────────────────────────────────────────

/** The buttons the state machine allows from the row's current state (Approve, Reject, …). A transition marked with a comment
 * asks for one first and sends it with the move. Server refusals (e.g. `illegal_transition`) are shown, never swallowed.
 * @category data
 * @example <StatusActions resource="expenses" row={row} onDone={reload} /> */
export function StatusActions<Row extends BaseRow>({ resource, row, onDone, small = true, className, ...rest }: { resource: Res<Row>; row: Row; onDone?: (row: Row) => void; small?: boolean; className?: string } & Attrs) {
  const def = resolve(resource)
  const sm = def.states
  const [pending, setPending] = useState<Transition | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!sm || !def.client.transition) return null
  const current = String(val(row, sm.field) ?? '')
  const options = sm.transitions.filter((t) => t.from === current || t.from === '*')
  if (options.length === 0) return null
  const move = async (t: Transition, text?: string) => {
    if (t.comment === 'required' && !(text ?? '').trim()) { setError('A comment is required'); return }
    setBusy(true)
    setError(null)
    try {
      const body = text && text.trim() ? { [t.commentField ?? 'comment']: text.trim() } : undefined
      const next = await (def.client.transition as NonNullable<ResourceClient<Row>['transition']>)(row.id, t.to, body)
      notify(`${t.label ?? titleCase(t.to)}: done`)
      setPending(null)
      setComment('')
      onDone?.(next)
    } catch (e) {
      const info = errorInfo(e)
      setError(info.message)
      notify(info.message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div data-vcomp="StatusActions" {...rest} className={cx('flex flex-col gap-2', className)} onClick={(e) => e.stopPropagation()}>
      <div className="flex flex-wrap gap-2">
        {options.map((t) => (
          <Btn key={t.to} small={small} tone={t.tone ?? 'default'} disabled={busy}
            onClick={() => (t.comment ? (setPending(pending?.to === t.to ? null : t), setError(null)) : move(t))}>{t.label ?? titleCase(t.to)}</Btn>
        ))}
      </div>
      {pending ? (
        <div className="flex flex-col gap-2">
          <textarea aria-label="Comment" rows={2} className={K.input} placeholder={pending.comment === 'required' ? 'Comment (required)' : 'Comment (optional)'} value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="flex gap-2">
            <Btn small tone={pending.tone ?? 'primary'} loading={busy} onClick={() => move(pending, comment)}>Confirm {(pending.label ?? titleCase(pending.to)).toLowerCase()}</Btn>
            <Btn small onClick={() => setPending(null)}>Cancel</Btn>
          </div>
        </div>
      ) : null}
      {error ? <span role="alert" className="text-xs text-[var(--danger)]">{error}</span> : null}
    </div>
  )
}

// ── RecordPanel ──────────────────────────────────────────────────────────────

/** Detail of one record: every field, its status actions, an edit form and a history built from `<state>_at` / `<state>_by`
 * columns. Renders nothing while `id` is null.
 * @category data
 * @example <RecordPanel resource="expenses" id={selectedId} onClose={() => setSelectedId(null)} /> */
export function RecordPanel<Row extends BaseRow>({ resource, id, onClose, onChanged, extra, className, bodyClassName, ...rest }: Omit<Attrs<HTMLElement>, 'id'> & {
  resource: Res<Row>
  id: number | null
  onClose?: () => void
  onChanged?: (row: Row) => void
  extra?: React.ReactNode
  className?: string
  bodyClassName?: string
}) {
  const def = resolve(resource)
  const [row, setRow] = useState<Row | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    setRow(null); setError(null); setEditing(false)
    if (id === null) return
    let alive = true
    const load = def.client.get ? def.client.get(id) : def.client.list({ id, limit: 1 }).then((p) => p.rows[0])
    load.then((r) => { if (alive) setRow(r) }, (e: unknown) => { if (alive) setError(errorInfo(e).message) })
    return () => { alive = false }
  }, [def, id])
  if (id === null) return null
  const changed = (r: Row) => { setRow(r); setEditing(false); onChanged?.(r) }
  const history = row ? def.fields.filter((f) => f.name.endsWith('_at') && val(row, f.name)).map((f) => {
    const who = val(row, f.name.replace(/_at$/, '_by'))
    return { label: titleCase(f.name.replace(/_at$/, '')), at: show(f, val(row, f.name)), who: who ? String(who) : '' }
  }) : []
  return (
    <Panel data-vcomp="RecordPanel" {...rest} className={className} bodyClassName={bodyClassName} title={row ? String(val(row, def.titleField) ?? `${def.singular} ${row.id}`) : def.singular}
      actions={<>
        {row && def.client.update && !editing ? <Btn small onClick={() => setEditing(true)}>Edit</Btn> : null}
        {onClose ? <Btn small onClick={onClose}>Close</Btn> : null}
      </>}>
      {error ? <Notice kind="error">{error}</Notice> : null}
      {!row && !error ? <p className={`text-sm ${K.muted}`}>Loading…</p> : null}
      {row && editing ? <ResourceForm resource={def} record={row} onSaved={changed} onCancel={() => setEditing(false)} /> : null}
      {row && !editing ? (
        <div className="flex flex-col gap-4">
          <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
            {def.fields.filter((f) => !f.name.endsWith('_by') && !f.name.endsWith('_at')).map((f) => (
              <div key={f.name} className="min-w-0">
                <dt className={`text-xs ${K.muted}`}>{f.label}</dt>
                <dd className="m-0 break-words text-sm">{def.states?.field === f.name ? <StateBadge value={val(row, f.name)} /> : show(f, val(row, f.name))}</dd>
              </div>
            ))}
          </dl>
          <StatusActions resource={def} row={row} small={false} onDone={changed} />
          {history.length ? (
            <div>
              <h3 className="m-0 mb-1 text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">History</h3>
              <ul className="m-0 list-none p-0 text-sm">
                {history.map((h) => <li key={h.label} className="py-0.5">{h.label} — {h.at}{h.who ? ` by ${h.who}` : ''}</li>)}
              </ul>
            </div>
          ) : null}
          {extra}
        </div>
      ) : null}
    </Panel>
  )
}

// ── StatTiles / GroupChart ───────────────────────────────────────────────────

export interface TileDef {
  label: string
  /** exact filters, e.g. `{ status: 'pending' }`; empty counts every record */
  filter?: Filters
  /** sum this numeric field over the matching records instead of counting them */
  sum?: string
  tone?: 'good' | 'warn' | 'bad' | 'none'
  /** not shown */
  hidden?: boolean
}

const TILE_COLS: Record<number, string> = { 2: 'lg:grid-cols-2', 3: 'lg:grid-cols-3', 4: 'lg:grid-cols-4' }

/** Row of KPI tiles: counts (from the list route's `total`) or sums over filtered records.
 * @category data
 * @example <StatTiles resource="expenses" tiles={[{ label: 'Pending', filter: { status: 'pending' }, tone: 'warn' }, { label: 'Total', sum: 'amount' }]} /> */
export function StatTiles<Row extends BaseRow>({ resource, tiles, reloadKey = 0, cols = 4, className, tileClassName, ...rest }: { resource: Res<Row>; tiles: TileDef[]; reloadKey?: number
  /** Tiles per row on wide screens (two on phones).
   * @control select @options 2|3|4 @group Layout @default 4 */
  cols?: 2 | 3 | 4
  className?: string
  /** Classes for every tile.
   * @control hidden @group Style */
  tileClassName?: string } & Attrs) {
  const def = resolve(resource)
  const [values, setValues] = useState<Array<number | null>>(tiles.map(() => null))
  const [error, setError] = useState<string | null>(null)
  const key = JSON.stringify(tiles)
  useEffect(() => {
    let alive = true
    Promise.all((JSON.parse(key) as TileDef[]).map(async (t) => {
      if (!t.sum) return (await def.client.list({ ...t.filter, limit: 1 })).total
      const page = await def.client.list({ ...t.filter, limit: 1000 })
      return page.rows.reduce((s, r) => s + (Number(val(r, t.sum as string)) || 0), 0)
    })).then((v) => { if (alive) { setValues(v); setError(null) } }, (e: unknown) => { if (alive) setError(errorInfo(e).message) })
    return () => { alive = false }
  }, [def, key, reloadKey])
  return (
    <div data-vcomp="StatTiles" {...rest} className={cx('grid grid-cols-2 gap-3', TILE_COLS[cols] ?? TILE_COLS[4], className)}>
      {error ? <div className="col-span-full"><Notice kind="error">{error}</Notice></div> : null}
        {tiles.map((t, i) => t.hidden ? null : (
          <div key={t.label} data-vpart={`tiles.${i}`} className={cx(`${K.card} p-4`, tileClassName)}>
            <div className={`text-xs font-medium uppercase tracking-wide ${K.muted}`}>{t.label}</div>
            <div className={`mt-1 text-2xl font-semibold ${t.tone === 'bad' ? 'text-[var(--danger)]' : t.tone === 'good' ? 'text-[var(--success)]' : t.tone === 'warn' ? 'text-[var(--warning)]' : ''}`}>
              {values[i] === null ? '…' : values[i]?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </div>
          </div>
        ))}
    </div>
  )
}

/** Horizontal bars: records per value of `groupBy` (or the sum of `sum` per value). Enum fields are counted per option from the
 * list route's `total`; other fields group up to 1000 records.
 * @category chart
 * @example <GroupChart resource="expenses" groupBy="category" sum="amount" title="Spend by category" /> */
export function GroupChart<Row extends BaseRow>({ resource, groupBy, sum, title, filter, reloadKey = 0, className, bodyClassName, ...rest }: Attrs<HTMLElement> & {
  resource: Res<Row>
  groupBy: string
  sum?: string
  title?: string
  filter?: Filters
  reloadKey?: number
  className?: string
  bodyClassName?: string
}) {
  const def = resolve(resource)
  const field = def.fields.find((f) => f.name === groupBy)
  const [bars, setBars] = useState<Array<{ label: string; value: number }> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fkey = JSON.stringify(filter ?? {})
  useEffect(() => {
    let alive = true
    const base = JSON.parse(fkey) as Filters
    const work = field?.enum && !sum
      ? Promise.all(field.enum.map(async (o) => ({ label: titleCase(o), value: (await def.client.list({ ...base, [groupBy]: o, limit: 1 })).total })))
      : def.client.list({ ...base, limit: 1000 }).then((p) => {
        const m = new Map<string, number>()
        for (const r of p.rows) {
          const k = String(val(r, groupBy) ?? '—')
          m.set(k, (m.get(k) ?? 0) + (sum ? Number(val(r, sum)) || 0 : 1))
        }
        return [...m.entries()].map(([label, value]) => ({ label, value }))
      })
    work.then((b) => { if (alive) setBars(b) }, (e: unknown) => { if (alive) setError(errorInfo(e).message) })
    return () => { alive = false }
  }, [def, groupBy, sum, fkey, reloadKey, field])
  const top = Math.max(1, ...(bars ?? []).map((b) => b.value))
  return (
    <Panel data-vcomp="GroupChart" {...rest} className={className} bodyClassName={bodyClassName} title={title ?? `${def.label} by ${field?.label.toLowerCase() ?? groupBy}`}>
      {error ? <Notice kind="error">{error}</Notice> : null}
      {!bars && !error ? <p className={`text-sm ${K.muted}`}>Loading…</p> : null}
      {bars && bars.length === 0 ? <p className={`text-sm ${K.muted}`}>Nothing to chart yet.</p> : null}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {bars?.map((b) => (
          <li key={b.label} className="flex items-center gap-3 text-sm">
            <span className="w-28 shrink-0 truncate">{b.label}</span>
            <span className="h-3 min-w-0 flex-1 rounded-full bg-[var(--muted)]">
              <span className="block h-3 rounded-full bg-[var(--primary)]" style={{ width: `${Math.max(2, (b.value / top) * 100)}%` }} />
            </span>
            <span className="w-14 shrink-0 text-right tabular-nums">{b.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

// ── ExportButton ─────────────────────────────────────────────────────────────

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Download the matching records as CSV (pages through the list route, 1000 at a time).
 * @category data
 * @example <ExportButton resource="expenses" filename="expenses.csv" /> */
export function ExportButton<Row extends BaseRow>({ resource, filters, filename, label = 'Export CSV', className, ...rest }: { resource: Res<Row>; filters?: Filters; filename?: string; label?: string; className?: string }
  & Omit<React.HTMLAttributes<HTMLButtonElement>, 'title' | 'children' | 'onClick' | 'resource' | 'content' | 'color'>) {
  const def = resolve(resource)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      const rows: Row[] = []
      for (let offset = 0; offset < 100000; offset += 1000) {
        const page = await def.client.list({ ...filters, limit: 1000, offset })
        rows.push(...page.rows)
        if (rows.length >= page.total || page.rows.length === 0) break
      }
      const cols = ['id', ...def.fields.map((f) => f.name), 'created_at', 'updated_at']
      const text = [cols.join(','), ...rows.map((r) => cols.map((c) => csvCell(val(r, c))).join(','))].join('\n')
      const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url
      a.download = filename ?? `${def.name}.csv`
      a.click()
      URL.revokeObjectURL(url)
      notify(`Exported ${rows.length} rows`)
    } catch (e) {
      notify(errorInfo(e).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return <Btn data-vcomp="ExportButton" {...rest} className={className} loading={busy} onClick={run}>{label}</Btn>
}

// ── Inbox ────────────────────────────────────────────────────────────────────

/** A work queue: the records in one state (tabs when several), each as a card with its key fields and the status actions, so
 * a reviewer can approve or reject without opening anything. Defaults to the state the first transition leaves.
 * @category data
 * @example <Inbox resource="expenses" states={['submitted']} fields={['title', 'amount']} /> */
export function Inbox<Row extends BaseRow>({ resource, states, fields, emptyText, onOpen, className, cardClassName, ...rest }: Attrs & {
  resource: Res<Row>
  states?: string[]
  fields?: string[]
  /** @control text */
  emptyText?: React.ReactNode
  onOpen?: (row: Row) => void
  className?: string
  /** Classes for every record card.
   * @control hidden @group Style */
  cardClassName?: string
}) {
  const def = resolve(resource)
  const sm = def.states
  const tabs = states ?? (sm ? [sm.transitions[0]?.from ?? sm.initial ?? ''] : [])
  const [tab, setTab] = useState(tabs[0] ?? '')
  const [nonce, setNonce] = useState(0)
  const query: ListQuery = { limit: 50, ...(sm && tab ? { [sm.field]: tab } : {}), _r: nonce }
  const { data, error, loading } = useResourceList(def, query)
  const fieldPart = partIndex(fields)
  const statePart = partIndex(states)
  const shown = (fields ? fields.map((n) => def.fields.find((f) => f.name === n)) : def.fields.filter((f) => !f.hidden && f.name !== def.titleField && f.name !== sm?.field).slice(0, 4))
    .filter((f): f is FieldDef => !!f)
  return (
    <div data-vcomp="Inbox" {...rest} className={cx('flex flex-col gap-3', className)}>
      {tabs.length > 1 ? (
        <div className="flex gap-2" role="tablist">
          {tabs.map((t) => <Btn key={t} small tone={t === tab ? 'primary' : 'default'} role="tab" aria-selected={t === tab} onClick={() => setTab(t)}
            data-vpart={statePart?.has(t) ? `states.${statePart.get(t)}` : undefined}>{titleCase(t)}</Btn>)}
        </div>
      ) : null}
      {error ? <Notice kind="error">{error.message}</Notice> : null}
      {loading && !data ? <p className={`text-sm ${K.muted}`}>Loading…</p> : null}
      {data && data.rows.length === 0 ? <div className={`${K.card} px-4 py-8 text-center text-sm ${K.muted}`}>{emptyText ?? 'Nothing waiting. You are all caught up.'}</div> : null}
      {data?.rows.map((row) => (
        <article key={row.id} data-vpart="card" className={cx(`${K.card} flex flex-col gap-3 p-4`, cardClassName)}>
          <header className="flex flex-wrap items-center gap-2">
            <h3 className="m-0 min-w-0 flex-1 truncate text-sm font-semibold">
              {onOpen ? <button type="button" className="underline-offset-2 hover:underline" onClick={() => onOpen(row)}>{String(val(row, def.titleField) ?? `${def.singular} ${row.id}`)}</button> : String(val(row, def.titleField) ?? `${def.singular} ${row.id}`)}
            </h3>
            {sm ? <StateBadge value={val(row, sm.field)} /> : null}
          </header>
          <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
            {shown.map((f) => (
              <div key={f.name} className="min-w-0" data-vpart={fieldPart?.has(f.name) ? `fields.${fieldPart.get(f.name)}` : undefined}><dt className={`text-xs ${K.muted}`}>{f.label}</dt><dd className="m-0 truncate text-sm">{show(f, val(row, f.name))}</dd></div>
            ))}
          </dl>
          <StatusActions resource={def} row={row} onDone={() => { setNonce((n) => n + 1) }} />
        </article>
      ))}
    </div>
  )
}

// ── Layouts ──────────────────────────────────────────────────────────────────

/** A page: title, one-line purpose, optional actions, then the content.
 * @category page
 * @example <Page title="Expenses" subtitle="Claims waiting for approval">Content</Page> */
export function Page({ title, subtitle, actions, children, className, ...rest }: { /** @control text */ title: React.ReactNode; /** @control text */ subtitle?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode; className?: string } & Omit<React.HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <div data-vcomp="Page" {...rest} className={className}>
      <PageHeader title={title} subtitle={subtitle} actions={actions} />
      {children}
    </div>
  )
}

export interface ShellScreen {
  id: string
  label: string
  /** the page; `go(id)` switches screen (e.g. to the list after a form is saved) */
  render: (go: (id: string) => void) => React.ReactNode
  /** only viewers with one of these roles see the screen (omit: everyone) */
  roles?: string[]
  /** not shown in the navigation */
  hidden?: boolean
}

/** App frame: side navigation (top bar on phones), the active screen and the toast host. Mount once as the root.
 * @category page
 * @example <AppShell title="Expenses" screens={[{ id: 'list', label: 'Claims', render: () => <ClaimsScreen /> }]} /> */
export function AppShell({ title, screens, viewerRoles, className, navClassName, bodyClassName, ...rest }: { title: string; screens: ShellScreen[]; viewerRoles?: string[]
  className?: string; navClassName?: string; bodyClassName?: string } & Attrs) {
  // `roles` screens are matched against viewerRoles, else the Studio preview's View-as user; neither known: every screen shows
  const viewer: unknown = viewerRoles ?? (globalThis as any).__studioViewer?.roles
  const roles = Array.isArray(viewer) ? viewer.map(String) : null
  const shown = screens.map((s, i) => ({ s, i }))
    .filter(({ s }) => !s.hidden && (!roles || !s.roles?.length || s.roles.some((r) => roles.includes(r))))
  const [current, setCurrent] = usePreviewState(`appshell:${title}`, shown[0]?.s.id ?? '', (id) => shown.some((x) => x.s.id === id))
  const active = shown.find((x) => x.s.id === current)?.s ?? shown[0]?.s
  return (
    <div data-vcomp="AppShell" {...rest} className={cx('flex min-h-screen flex-col bg-[var(--background)] text-[var(--foreground)] md:flex-row', className)}>
      <nav data-vpart="nav" className={cx('flex shrink-0 gap-1 overflow-x-auto border-b border-[var(--border)] bg-[var(--card)] p-3 md:w-56 md:flex-col md:border-b-0 md:border-r', navClassName)}>
        <div data-vpart="title" className="hidden px-2 pb-3 text-sm font-semibold md:block">{title}</div>
        {shown.map(({ s, i }) => (
          <button key={s.id} type="button" data-vpart={`screens.${i}`} onClick={() => setCurrent(s.id)} aria-current={s.id === active?.id ? 'page' : undefined}
            className={`whitespace-nowrap rounded-[var(--radius)] px-3 py-2 text-left text-sm ${s.id === active?.id ? 'bg-[var(--primary)] text-[var(--primary-foreground)]' : 'text-[var(--muted-foreground)] hover:bg-[var(--muted)]'}`}>
            {s.label}
          </button>
        ))}
        <ColorSchemeToggle className="ml-auto shrink-0 self-center md:ml-0 md:mt-auto md:self-start" />
      </nav>
      <main data-vpart="body" className={cx('min-w-0 flex-1 p-4 md:p-6', bodyClassName)}>{active ? active.render(setCurrent) : null}</main>
      <ToastHost />
    </div>
  )
}

/** Table of a resource with the record beside it: click a row to open it (edit, status actions, history), "New" opens the
 * create form, Export downloads CSV. Create/edit/transition buttons appear only when the resource's routes exist.
 * @category page
 * @example <ListDetailLayout resource="expenses" title="Expenses" columns={['title', 'amount', 'status']} createLabel="New claim" /> */
export function ListDetailLayout<Row extends BaseRow>({ resource, title, subtitle, columns, filters, emptyText, pageSize, createLabel, exportable = true, className, ...rest }: Attrs & {
  resource: Res<Row>
  title: string
  subtitle?: string
  columns?: string[]
  filters?: Filters
  /** @control text */
  emptyText?: React.ReactNode
  pageSize?: number
  createLabel?: string
  exportable?: boolean
  className?: string
}) {
  const def = resolve(resource)
  const [selected, setSelected] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [tick, setTick] = useState(0)
  const side = selected !== null || creating
  return (
    <Page data-vcomp="ListDetailLayout" {...rest} className={className} title={title} subtitle={subtitle} actions={exportable ? <ExportButton resource={def} filters={filters} /> : undefined}>
      <div className={`grid grid-cols-1 gap-4 ${side ? 'lg:grid-cols-[minmax(0,1fr)_380px]' : ''}`}>
        <ResourceTable resource={def} columns={columns} filters={filters} pageSize={pageSize} emptyText={emptyText} reloadKey={tick} selectedId={selected}
          createLabel={createLabel} onSelect={(row) => { setSelected(row.id); setCreating(false) }}
          onCreate={def.client.create ? () => { setCreating(true); setSelected(null) } : undefined} />
        {creating ? (
          <Panel title={`New ${def.singular.toLowerCase()}`}>
            <ResourceForm resource={def} onSaved={() => { setCreating(false); setTick((t) => t + 1) }} onCancel={() => setCreating(false)} />
          </Panel>
        ) : <RecordPanel resource={def} id={selected} onClose={() => setSelected(null)} onChanged={() => setTick((t) => t + 1)} />}
      </div>
    </Page>
  )
}

/** KPI tiles, a chart of the records per `groupBy` value and a short recent list.
 * @category page
 * @example <DashboardLayout resource="expenses" title="Overview" tiles={[{ label: 'Pending', filter: { status: 'pending' } }]} groupBy="category" recentColumns={['title', 'amount']} /> */
export function DashboardLayout<Row extends BaseRow>({ resource, title, subtitle, tiles, groupBy, sum, recentColumns, className, ...rest }: Attrs & {
  resource: Res<Row>
  title: string
  subtitle?: string
  tiles: TileDef[]
  groupBy?: string
  sum?: string
  recentColumns?: string[]
  className?: string
}) {
  const def = resolve(resource)
  return (
    <Page data-vcomp="DashboardLayout" {...rest} className={className} title={title} subtitle={subtitle}>
      <div className="flex flex-col gap-4">
        <StatTiles resource={def} tiles={tiles} />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {groupBy ? <GroupChart resource={def} groupBy={groupBy} sum={sum} /> : null}
          <div className="min-w-0">
            <ColumnsPart.Provider value="recentColumns">
              <ResourceTable resource={def} pageSize={5} columns={recentColumns} emptyText="Nothing to show yet." />
            </ColumnsPart.Provider>
          </div>
        </div>
      </div>
    </Page>
  )
}

/** A create form on its own page. `onDone` runs after a successful save (e.g. `() => go('my-requests')`).
 * @category page
 * @example <FormPage resource="expenses" title="New claim" fields={['title', 'amount']} submitLabel="Submit" /> */
export function FormPage<Row extends BaseRow>({ resource, title, subtitle, fields, submitLabel, onDone, className, ...rest }: Attrs & {
  resource: Res<Row>
  title: string
  subtitle?: string
  fields?: string[]
  submitLabel?: string
  onDone?: (row: Row) => void
  className?: string
}) {
  return (
    <Page data-vcomp="FormPage" {...rest} className={className} title={title} subtitle={subtitle}>
      <div className="max-w-xl"><Panel><ResourceForm resource={resource} fields={fields} submitLabel={submitLabel} onSaved={onDone} /></Panel></div>
    </Page>
  )
}

/** A work queue of the records in one state (tabs for several) with their status actions on each card.
 * @category page
 * @example <InboxLayout resource="expenses" title="To approve" states={['submitted']} fields={['title', 'amount']} /> */
export function InboxLayout<Row extends BaseRow>({ resource, title, subtitle, states, fields, emptyText, className, ...rest }: Attrs & {
  resource: Res<Row>
  title: string
  subtitle?: string
  states?: string[]
  fields?: string[]
  /** @control text */
  emptyText?: React.ReactNode
  className?: string
}) {
  return (
    <Page data-vcomp="InboxLayout" {...rest} className={className} title={title} subtitle={subtitle}>
      <Inbox resource={resource} states={states} fields={fields} emptyText={emptyText} />
    </Page>
  )
}

/** Edit the single settings record (the first row; created when none exists).
 * @category page
 * @example <SettingsPage resource="settings" title="Settings" /> */
export function SettingsPage<Row extends BaseRow>({ resource, title, subtitle, fields, className, ...rest }: { resource: Res<Row>; title: string; subtitle?: string; fields?: string[]; className?: string } & Attrs) {
  const { data, loading, error, reload } = useResourceList(resource)
  const current = data && data.rows.length ? data.rows[0] : null
  return (
    <Page data-vcomp="SettingsPage" {...rest} className={className} title={title} subtitle={subtitle}>
      <div className="max-w-xl">
        <Panel>
          {error ? <Notice kind="error">{error.message}</Notice> : null}
          {loading && !data ? <p className={`text-sm ${K.muted}`}>Loading…</p> : null}
          {data ? <ResourceForm resource={resource} record={current} fields={fields} onSaved={reload} submitLabel="Save settings" /> : null}
        </Panel>
      </div>
    </Page>
  )
}
