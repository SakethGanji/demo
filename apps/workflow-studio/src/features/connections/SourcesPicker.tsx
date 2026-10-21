import { Badge } from '@/shared/components/ui/badge'
import { useState } from 'react'
import { Database, X } from 'lucide-react'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { cn } from '@/shared/lib/utils'
import { buildSources, showSourcesHint, sourceChipLabel, tableId, toggleConnection, toggleTable, type SourceSelection } from './connectionsLogic'
import type { Connection } from './connectionsTypes'
import { useConnectionSchema, useConnections } from './useConnections'

function TableChoices({ pid, c, chosen, onToggle }: { pid: string; c: Connection; chosen: string[]; onToggle: (t: string) => void }) {
  const schema = useConnectionSchema(pid, c.id)
  const tables = schema.data?.tables ?? []
  if (schema.isPending) return <div className="px-2 py-1 text-[12px] text-muted-foreground">Loading tables…</div>
  if (!tables.length) return <div className="px-2 py-1 text-[12px] text-muted-foreground">No tables read yet.</div>
  return (
    <div className="ml-6 flex max-h-40 flex-col overflow-y-auto" data-testid={`sources-tables-${c.key}`}>
      <span className="px-2 pb-0.5 text-[11.5px] text-muted-foreground">{chosen.length ? `${chosen.length} chosen` : 'All tables (tick to narrow)'}</span>
      {tables.map((t) => {
        const id = tableId(t)
        return (
          <label key={id} className="flex h-7 cursor-pointer items-center gap-2 rounded-ctl px-2 text-[12.5px] hover:bg-foreground/4">
            <Checkbox checked={chosen.includes(id)} onCheckedChange={() => onToggle(id)} />
            <span className="truncate font-mono">{id}</span>
          </label>
        )
      })}
    </div>
  )
}

/**
 * "Use data from" for the plan composer: connection -> tables multi-select. Controlled by the caller so it can send
 * `buildSources(selection)` with the first message. Renders nothing when the project has no connections.
 */
export function SourcesPicker({ pid, value, onChange, disabled, className }: { pid: string; value: SourceSelection; onChange: (v: SourceSelection) => void; disabled?: boolean; className?: string }) {
  const q = useConnections(pid)
  const [open, setOpen] = useState(false)
  const list = q.data ?? []
  if (!list.length) return null
  const sources = buildSources(value, list) ?? []
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)} data-testid="sources-picker">
      {sources.map((s) => (
        <Badge key={s.connection} variant="neutral" className="h-6 pl-2 pr-1 text-[12px] font-normal text-foreground" data-testid={`source-chip-${s.connection}`}>
          <Database className="size-3 text-muted-foreground" />
          {sourceChipLabel(s)}
          <button type="button" aria-label={`Remove ${s.connection}`} className="grid size-4 place-items-center rounded-full hover:bg-foreground/8" onClick={() => onChange(toggleConnection(value, s.connection))}>
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          disabled={disabled}
          className="inline-flex h-6 items-center gap-1 rounded-full px-2 text-[12px] text-muted-foreground hover:bg-foreground/6 hover:text-foreground disabled:opacity-50"
          data-testid="sources-open"
        >
          <Database className="size-3" />
          {showSourcesHint(list, value) ? 'Use data from your databases' : 'Use data from'}
        </PopoverTrigger>
        <PopoverContent align="start" className="w-72 p-2" data-testid="sources-popover">
          {list.map((c) => {
            const on = c.key in value
            return (
              <div key={c.id} className="flex flex-col gap-0.5">
                <label className="flex h-8 cursor-pointer items-center gap-2 rounded-ctl px-2 text-[13px] hover:bg-foreground/4">
                  <Checkbox checked={on} onCheckedChange={() => onChange(toggleConnection(value, c.key))} data-testid={`sources-conn-${c.key}`} />
                  <span className="truncate">{c.name}</span>
                  <span className="ml-auto text-[11.5px] text-muted-foreground">{c.table_count} tables</span>
                </label>
                {on && <TableChoices pid={pid} c={c} chosen={value[c.key] ?? []} onToggle={(t) => onChange(toggleTable(value, c.key, t))} />}
              </div>
            )
          })}
        </PopoverContent>
      </Popover>
    </div>
  )
}
