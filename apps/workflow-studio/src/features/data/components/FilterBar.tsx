/**
 * Filter bar (UI-DESIGN §3.4): `+ Filter` builds clauses from the column types, in the engine
 * grammar (see lib/filters.ts); chips remove them; "match all / any" picks `$and` / `$or`.
 * The sort button flips the current sort (column headers set it).
 */
import { useState } from 'react'
import { ArrowDownWideNarrow, ArrowUpNarrowWide, ListFilter, X } from 'lucide-react'
import type { Column } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Segmented } from '@/shared/components/ui/segmented'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import {
  OP_LABEL,
  clauseLabel,
  columnType,
  opsFor,
  parseClauseValue,
  parseSort,
  sortLabel,
  type Clause,
  type FilterModel,
  type FilterOp,
} from '../lib/filters'

export function FilterBar({
  columns,
  model,
  onChange,
  sort,
  onSort,
}: {
  columns: Column[]
  model: FilterModel
  onChange: (m: FilterModel) => void
  sort: string | undefined
  onSort: (s: string) => void
}) {
  const { key, desc } = parseSort(sort)
  const SortIcon = desc ? ArrowDownWideNarrow : ArrowUpNarrowWide
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5" data-testid="data-filter-bar">
      {model.raw ? (
        <span
          className="inline-flex h-6 max-w-[360px] items-center gap-1 rounded-md border border-border bg-muted/50 pr-1 pl-2 text-xs"
          title={JSON.stringify(model.raw)}
          data-testid="filter-chip-custom"
        >
          <span className="truncate font-mono">{JSON.stringify(model.raw)}</span>
          <Button variant="ghost" size="icon-xs" className="size-4" aria-label="Clear filter" onClick={() => onChange({ join: '$and', clauses: [], raw: null })}>
            <X />
          </Button>
        </span>
      ) : (
        model.clauses.map((c, i) => (
          <span
            key={i}
            className="inline-flex h-6 max-w-[280px] items-center gap-1 rounded-md border border-primary/30 bg-primary/6 pr-1 pl-2 text-xs"
            data-testid="filter-chip"
          >
            <span className="truncate font-mono">{clauseLabel(c)}</span>
            <Button
              variant="ghost"
              size="icon-xs"
              className="size-4"
              aria-label={`Remove ${clauseLabel(c)}`}
              onClick={() => onChange({ ...model, clauses: model.clauses.filter((_, j) => j !== i) })}
              data-testid="filter-chip-remove"
            >
              <X />
            </Button>
          </span>
        ))
      )}
      {model.clauses.length > 1 && !model.raw && (
        <Segmented<'$and' | '$or'>
          size="xs"
          aria-label="Combine filters"
          value={model.join}
          onChange={(join) => onChange({ ...model, join })}
          options={[
            { key: '$and', label: 'match all', testId: 'filter-join-and' },
            { key: '$or', label: 'match any', testId: 'filter-join-or' },
          ]}
        />
      )}
      <AddFilter
        columns={columns}
        onAdd={(c) => onChange({ join: model.join, raw: null, clauses: [...(model.raw ? [] : model.clauses), c] })}
      />
      <Button
        variant="ghost"
        size="xs"
        className="text-muted-foreground"
        onClick={() => onSort(`${desc ? '' : '-'}${key}`)}
        data-testid="data-sort"
        data-sort={sort ?? '-created_at'}
      >
        <SortIcon /> {sortLabel(sort)}
      </Button>
    </div>
  )
}

function AddFilter({ columns, onAdd }: { columns: Column[]; onAdd: (c: Clause) => void }) {
  const [open, setOpen] = useState(false)
  const names = [...columns.map((c) => c.name), 'id']
  const [column, setColumn] = useState(names[0])
  const t = columnType(columns, column)
  const ops = opsFor(t)
  const [op, setOp] = useState<FilterOp>(ops[0])
  const [text, setText] = useState('')
  const curOp = ops.includes(op) ? op : ops[0]
  const boolValue = curOp === '$exists' || (t === 'boolean' && curOp !== '$in')

  const submit = () => {
    if (!boolValue && curOp !== '$contains' && text.trim() === '') return
    onAdd({ column, op: curOp, value: parseClauseValue(t, curOp, boolValue ? text || 'true' : text) })
    setText('')
    setOpen(false)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (o && !names.includes(column)) setColumn(names[0])
      }}
    >
      <PopoverTrigger
        render={
          <Button variant="ghost" size="xs" className="text-muted-foreground" data-testid="data-add-filter">
            <ListFilter /> Filter
          </Button>
        }
      />
      <PopoverContent className="w-[360px]" align="start">
        <form
          className="flex flex-col gap-2.5"
          data-testid="filter-form"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <div className="grid grid-cols-[minmax(0,1fr)_110px] gap-2">
            <Select
              value={column}
              onValueChange={(v) => {
                if (!v) return
                setColumn(String(v))
                setText('')
              }}
              items={names.map((n) => ({ value: n, label: n }))}
            >
              <SelectTrigger size="sm" className="h-7 w-full font-mono text-xs" data-testid="filter-column">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {names.map((n) => (
                  <SelectItem key={n} value={n} className="font-mono text-xs" data-testid={`filter-column-${n}`}>
                    {n}
                    <span className="ml-auto pl-3 font-sans text-[11px] text-muted-foreground">{columnType(columns, n)}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={curOp}
              onValueChange={(v) => v && setOp(v as FilterOp)}
              items={ops.map((o) => ({ value: o, label: OP_LABEL[o] }))}
            >
              <SelectTrigger size="sm" className="h-7 w-full text-xs" data-testid="filter-op">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ops.map((o) => (
                  <SelectItem key={o} value={o} className="text-xs" data-testid={`filter-op-${o.slice(1)}`}>
                    {OP_LABEL[o]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {boolValue ? (
            <Segmented<'true' | 'false'>
              size="xs"
              aria-label="Value"
              value={text === 'false' ? 'false' : 'true'}
              onChange={setText}
              options={
                curOp === '$exists'
                  ? [
                      { key: 'true', label: 'has a value', testId: 'filter-bool-true' },
                      { key: 'false', label: 'is absent', testId: 'filter-bool-false' },
                    ]
                  : [
                      { key: 'true', label: 'true', testId: 'filter-bool-true' },
                      { key: 'false', label: 'false', testId: 'filter-bool-false' },
                    ]
              }
            />
          ) : (
            <Input
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={curOp === '$in' ? 'a, b, c' : t === 'datetime' ? '2026-10-15T09:00:00' : 'value'}
              className="h-7 font-mono text-xs"
              inputMode={t === 'number' || t === 'integer' || t === 'id' ? 'decimal' : undefined}
              data-testid="filter-value"
            />
          )}
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[11px] text-muted-foreground">
              {JSON.stringify({ [column]: { [curOp]: parseClauseValue(t, curOp, boolValue ? text || 'true' : text) } })}
            </span>
            <Button type="submit" size="xs" data-testid="filter-apply">
              Add filter
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}
