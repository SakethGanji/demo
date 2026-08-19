/**
 * The studio command palette (⌘K).
 *
 * Scoped honestly to what the studio can actually do today: jump to a route,
 * and jump to a dataset. The prototype (`terminal-cmdk.html`) also lists a
 * Columns group, an Actions group and a Recent group — those need a column
 * index, a command registry and a history store respectively, none of which
 * exist yet. Rendering empty or fake versions of them would make the palette
 * look finished while teaching the user that it does not work.
 *
 * Dataset results come from the live catalog, seat-scoped like every other
 * read, so what a viewer sees here is what a viewer can open. The search is
 * server-side (`?q=`) rather than a client filter over a cached page, because
 * a client filter would silently only search whatever happened to be loaded.
 */

import { useNavigate } from '@tanstack/react-router'
import {
  Activity,
  Clock,
  Columns3,
  Database,
  FileUp,
  Filter,
  LayoutGrid,
  Shield,
  Sigma,
  Split,
  Table2,
  Workflow,
} from 'lucide-react'
import { useState } from 'react'

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/shared/components/ui/command'
import { useQuery } from '@tanstack/react-query'
import { analytics, type Page } from '@/shared/lib/analyticsClient'
import { useIdentityStore } from '@/shared/lib/identity'
import { useDatasetCatalog } from '@/features/datasets/hooks/useDatasets'
import { compact } from '@/shared/lib/format'

/**
 * A column, found across every dataset this seat can read.
 *
 * `GET /search/columns` is a real endpoint that nothing was calling — the
 * prototype's palette has a Columns group for exactly this, and searching for
 * a column by name is how people actually find the dataset they want when they
 * remember the field but not the file.
 *
 * Seat-scoped like every other read, so a viewer's results are a viewer's
 * results. `q` is required by the API, so the query stays disabled until there
 * is something to ask.
 */
interface ColumnHit {
  dataset_id: string
  dataset_name: string
  domain?: string | null
  sheet_name: string
  column_name: string
  dtype?: string | null
  position?: number | null
}

function useColumnSearch(q: string, enabled: boolean) {
  const seat = useIdentityStore((s) => s.identity.userId)
  return useQuery({
    queryKey: ['analytics', seat, 'search-columns', q],
    queryFn: () => analytics.get<Page<ColumnHit>>('/search/columns', { q, limit: 6 }),
    enabled: enabled && q.length >= 2,
    retry: false,
  })
}

const ROUTES = [
  { to: '/projects', label: 'Workflows', icon: Workflow },
  { to: '/data', label: 'Datasets', icon: Table2 },
  { to: '/catalog', label: 'Catalog', icon: LayoutGrid },
  { to: '/runs', label: 'Runs', icon: Activity },
  { to: '/agents', label: 'Agents', icon: Activity },
  { to: '/build', label: 'Build (Script → Workflow)', icon: Workflow },
  { to: '/admin', label: 'Governance', icon: Shield },
] as const

/**
 * Tools that act on a dataset. They are absent from the header nav on purpose
 * — this is where you reach them, which is what a command palette is for.
 */
const TOOLS = [
  { to: '/query', label: 'Query and filter', icon: Filter },
  { to: '/aggregate', label: 'Aggregate', icon: Sigma },
  { to: '/pivot', label: 'Pivot and SQL', icon: LayoutGrid },
  { to: '/column', label: 'Column deep-dive', icon: Split },
  { to: '/sampling', label: 'Sampling', icon: Database },
  { to: '/ingest', label: 'Ingest data', icon: FileUp },
] as const

/**
 * Recently opened datasets.
 *
 * The prototype's palette has a Recent group and there is no history endpoint,
 * so this is client-side and says nothing it cannot know: it is what THIS
 * browser opened, not what the team did. Seat-scoped, because a viewer and an
 * admin do not share a history any more than they share a result set.
 */
const RECENT_KEY = 'studio.recentDatasets'
const RECENT_MAX = 5

export function rememberDataset(seat: string, id: string, name: string) {
  try {
    const raw = localStorage.getItem(`${RECENT_KEY}.${seat}`)
    const prev: { id: string; name: string }[] = raw ? JSON.parse(raw) : []
    const next = [{ id, name }, ...prev.filter((r) => r.id !== id)].slice(0, RECENT_MAX)
    localStorage.setItem(`${RECENT_KEY}.${seat}`, JSON.stringify(next))
  } catch {
    // Storage unavailable or full: recency is a convenience, never a blocker.
  }
}

function readRecent(seat: string): { id: string; name: string }[] {
  try {
    const raw = localStorage.getItem(`${RECENT_KEY}.${seat}`)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export function StudioCommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const seat = useIdentityStore((st) => st.identity.userId)
  const [query, setQuery] = useState('')

  const trimmed = query.trim()
  // Only ask the server once there is something to ask about; an empty palette
  // should not fetch the whole catalog on every ⌘K.
  const catalog = useDatasetCatalog(trimmed ? { q: trimmed } : {})
  const datasets = open ? (catalog.data?.items ?? []).slice(0, 6) : []

  const columns = useColumnSearch(trimmed, open)
  const columnHits = open ? (columns.data?.items ?? []) : []
  const searching = trimmed.length > 0
  const recent = open && !searching ? readRecent(seat) : []

  const go = (to: string) => {
    onOpenChange(false)
    setQuery('')
    void navigate({ to })
  }

  const openColumn = (datasetId: string, column: string) => {
    onOpenChange(false)
    setQuery('')
    void navigate({ to: '/column', search: { dataset: datasetId, column } })
  }

  const openDataset = (id: string) => {
    onOpenChange(false)
    setQuery('')
    void navigate({ to: '/data', search: { dataset: id } })
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      {/* cmdk filters client-side by default, which would fight the server-side
       * `?q=` search above and hide datasets the server did return. */}
      <Command shouldFilter={false}>
        <CommandInput
          placeholder="Search datasets, run a command…"
          value={query}
          onValueChange={setQuery}
        />
        <CommandList>
          <CommandEmpty>No matches.</CommandEmpty>

          {/* Results outrank navigation once there is a query. Typing a
            * column name and getting a static nav list first is the palette
            * answering a question nobody asked. */}
          {searching ? (
            <>
          {datasets.length > 0 && (
            <CommandGroup heading="Datasets">
              {datasets.map((d) => (
                <CommandItem
                  key={d.id}
                  value={`dataset ${d.name} ${d.id}`}
                  onSelect={() => openDataset(d.id)}
                >
                  <Database className="size-3.5 text-muted-foreground" />
                  <span className="truncate">{d.name}</span>
                  <span className="ml-auto shrink-0 font-mono text-micro text-muted-foreground tabular-nums">
                    {compact(d.row_count)}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {columnHits.length > 0 && (
            <CommandGroup heading="Columns">
              {columnHits.map((c) => (
                <CommandItem
                  key={`${c.dataset_id}-${c.sheet_name}-${c.column_name}`}
                  value={`column ${c.column_name} ${c.dataset_name}`}
                  onSelect={() => openColumn(c.dataset_id, c.column_name)}
                >
                  <Columns3 className="size-3.5 shrink-0 text-muted-foreground" />
                  {/* The column name is what was searched for, so it never
                    * truncates before its context does. */}
                  <span className="shrink-0 font-mono">{c.column_name}</span>
                  <span className="min-w-0 flex-1 truncate text-micro text-muted-foreground">
                    {c.dataset_name} · {c.sheet_name}
                  </span>
                  {c.dtype && (
                    <span className="ml-auto shrink-0 font-mono text-micro text-muted-foreground">
                      {c.dtype}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          <CommandGroup heading="Go to">
            {ROUTES.map(({ to, label, icon: Icon }) => (
              <CommandItem key={to} value={`goto ${label}`} onSelect={() => go(to)}>
                <Icon className="size-3.5 text-muted-foreground" />
                {label}
              </CommandItem>
            ))}
          </CommandGroup>

          <CommandGroup heading="Tools">
            {TOOLS.map(({ to, label, icon: Icon }) => (
              <CommandItem key={to} value={`tool ${label}`} onSelect={() => go(to)}>
                <Icon className="size-3.5 text-muted-foreground" />
                {label}
              </CommandItem>
            ))}
          </CommandGroup>

            </>
          ) : (
            <>
              {recent.length > 0 && (
                <CommandGroup heading="Recent">
                  {recent.map((r) => (
                    <CommandItem
                      key={r.id}
                      value={`recent ${r.name}`}
                      onSelect={() => openDataset(r.id)}
                    >
                      <Clock className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{r.name}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}

          <CommandGroup heading="Go to">
            {ROUTES.map(({ to, label, icon: Icon }) => (
              <CommandItem key={to} value={`goto ${label}`} onSelect={() => go(to)}>
                <Icon className="size-3.5 text-muted-foreground" />
                {label}
              </CommandItem>
            ))}
          </CommandGroup>

          <CommandGroup heading="Tools">
            {TOOLS.map(({ to, label, icon: Icon }) => (
              <CommandItem key={to} value={`tool ${label}`} onSelect={() => go(to)}>
                <Icon className="size-3.5 text-muted-foreground" />
                {label}
              </CommandItem>
            ))}
          </CommandGroup>

            </>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
