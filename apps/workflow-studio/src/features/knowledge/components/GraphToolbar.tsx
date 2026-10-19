/**
 * The floating glass chrome over the canvas: search and kind chips on the left; relations, "learned only" and
 * Rebuild on the right. Zoom controls and the graph stats sit at the bottom corners (`ZoomControls`, `GraphStats`).
 */
import { Filter, Maximize, Minus, Plus, RefreshCw, Search, X } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/shared/components/ui/input-group'
import { Spinner } from '@/shared/components/ui/spinner'
import { Switch } from '@/shared/components/ui/switch'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { SEARCH_ID } from '../lib/search'
import { KIND_ORDER, KIND_STYLE, relStyle, type KindGroup } from '../lib/kinds'
import type { ProjectChipModel } from '../lib/projects'
import { KindSwatch } from './KindSwatch'

export function SearchBox({ value, onChange, onSubmit, matches, className }: {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  matches: number | null
  className?: string
}) {
  return (
    <InputGroup className={cn('h-8 w-56 bg-transparent', className)}>
      <InputGroupAddon>
        <Search className="size-3.5" />
      </InputGroupAddon>
      <InputGroupInput
        id={SEARCH_ID}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            onSubmit()
          } else if (e.key === 'Escape' && value) {
            e.stopPropagation()
            onChange('')
          }
        }}
        placeholder="Find a concept, node, pattern"
        aria-label="Search the graph"
        data-testid="knowledge-search"
        className="text-[12.5px]"
      />
      {value && (
        <InputGroupAddon align="inline-end">
          <span className="text-[11px] tabular-nums text-muted-foreground" aria-live="polite" data-testid="knowledge-search-count">
            {matches === 0 ? 'none' : matches}
          </span>
          <InputGroupButton size="icon-xs" aria-label="Clear search" onClick={() => onChange('')}>
            <X />
          </InputGroupButton>
        </InputGroupAddon>
      )}
    </InputGroup>
  )
}

export function KindChips({
  counts, hidden, onToggle,
}: {
  counts: Record<KindGroup, number>
  hidden: ReadonlySet<KindGroup>
  onToggle: (k: KindGroup) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-0.5" role="group" aria-label="Show kinds">
      {KIND_ORDER.filter((k) => counts[k] > 0).map((k) => {
        const on = !hidden.has(k)
        return (
          <button
            key={k}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(k)}
            data-testid={`knowledge-kind-${k}`}
            className={cn(
              'inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium outline-none transition-[opacity,background-color] duration-(--d-fast) hover:bg-(--hover-fill) focus-visible:shadow-focus',
              on ? 'text-foreground' : 'text-muted-foreground opacity-55',
            )}
          >
            <KindSwatch kind={k} size={10} dashed={!on} />
            {KIND_STYLE[k].plural}
            <span className="tabular-nums text-muted-foreground">{counts[k]}</span>
          </button>
        )
      })}
    </div>
  )
}

export function RelationMenu({
  rels, hidden, onToggle, onShowAll,
}: {
  rels: [string, number][]
  hidden: ReadonlySet<string>
  onToggle: (rel: string) => void
  onShowAll: () => void
}) {
  const n = hidden.size
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button size="sm" variant="ghost" data-testid="knowledge-relations" />}>
        <Filter /> Relations
        {n > 0 && <span className="tabular-nums text-muted-foreground">{rels.length - n}/{rels.length}</span>}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-52">
        <DropdownMenuLabel>Show relations</DropdownMenuLabel>
        {rels.map(([rel, count]) => (
          <DropdownMenuCheckboxItem
            key={rel}
            checked={!hidden.has(rel)}
            onCheckedChange={() => onToggle(rel)}
            closeOnClick={false}
            data-testid={`knowledge-rel-${rel}`}
          >
            <RelGlyph rel={rel} />
            <span className="flex-1">{relStyle(rel).label}</span>
            <span className="tabular-nums text-muted-foreground">{count}</span>
          </DropdownMenuCheckboxItem>
        ))}
        {n > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onShowAll}>Show all</DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** A short sample of how the relation's line is drawn. */
export function RelGlyph({ rel }: { rel: string }) {
  const rs = relStyle(rel)
  return (
    <svg width={18} height={6} className="shrink-0 text-muted-foreground" aria-hidden>
      <line x1={1} y1={3} x2={17} y2={3} stroke="currentColor" strokeWidth={rs.width + 0.25} strokeDasharray={rs.dash || undefined} strokeLinecap="round" />
    </svg>
  )
}

export function LearnedToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 px-1.5 text-[12.5px] font-medium text-foreground/85">
      <Switch checked={checked} onCheckedChange={onChange} data-testid="knowledge-learned-only" aria-label="Learned only" />
      Learned only
    </label>
  )
}

export function RebuildButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <Tip label="Re-read the catalogue, templates and kept builds into the graph">
      <Button size="sm" variant="outline" onClick={onClick} disabled={busy} data-testid="knowledge-rebuild">
        {busy ? <Spinner className="size-3.5" /> : <RefreshCw />}
        {busy ? 'Rebuilding' : 'Rebuild'}
      </Button>
    </Tip>
  )
}

export function ZoomControls({ onIn, onOut, onFit }: { onIn: () => void; onOut: () => void; onFit: () => void }) {
  const btn = 'size-8 rounded-full'
  return (
    <div className="glass-chrome flex flex-col items-center gap-0.5 rounded-full p-1" role="group" aria-label="Zoom">
      <Tip label="Zoom in" side="left">
        <Button size="icon" variant="ghost" className={btn} onClick={onIn} aria-label="Zoom in" data-testid="knowledge-zoom-in">
          <Plus />
        </Button>
      </Tip>
      <Tip label="Zoom out" side="left">
        <Button size="icon" variant="ghost" className={btn} onClick={onOut} aria-label="Zoom out" data-testid="knowledge-zoom-out">
          <Minus />
        </Button>
      </Tip>
      <Tip label="Fit to view" kbd="F" side="left">
        <Button size="icon" variant="ghost" className={btn} onClick={onFit} aria-label="Fit to view" data-testid="knowledge-fit">
          <Maximize />
        </Button>
      </Tip>
    </div>
  )
}

/**
 * Team scope: one chip per project (and "Shared" for knowledge no project owns), with its node count. Pressing a
 * chip hides or shows that project's nodes; the colour dot is the same one the nodes carry on the canvas.
 */
export function ProjectChips({
  chips, hidden, onToggle, onShowAll,
}: {
  chips: ProjectChipModel[]
  hidden: ReadonlySet<string>
  onToggle: (key: string) => void
  onShowAll: () => void
}) {
  if (!chips.length) return null
  return (
    <div className="flex flex-wrap items-center gap-0.5" role="group" aria-label="Show projects" data-testid="knowledge-projects">
      <span className="px-1.5 text-[12px] font-medium text-muted-foreground">Projects</span>
      {chips.map((c) => {
        const on = !hidden.has(c.key)
        return (
          <button
            key={c.key}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(c.key)}
            data-testid={`knowledge-project-${c.key}`}
            className={cn(
              'inline-flex h-7 max-w-48 min-w-0 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium outline-none transition-[opacity,background-color] duration-(--d-fast) hover:bg-(--hover-fill) focus-visible:shadow-focus',
              on ? 'text-foreground' : 'text-muted-foreground opacity-55',
            )}
          >
            <span
              className={cn('inline-block size-2 shrink-0 rounded-full', !on && 'opacity-50')}
              style={{ backgroundColor: c.color }}
              aria-hidden
            />
            <span className="truncate">{c.label}</span>
            <span className="tabular-nums text-muted-foreground">{c.count}</span>
          </button>
        )
      })}
      {hidden.size > 0 && (
        <Button size="xs" variant="ghost" onClick={onShowAll} data-testid="knowledge-projects-all">
          Show all
        </Button>
      )}
    </div>
  )
}

/** Team scope: gather each project's nodes round its own anchor, shared knowledge in the middle. */
export function GroupToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 px-1.5 text-[12.5px] font-medium text-foreground/85">
      <Switch checked={checked} onCheckedChange={onChange} data-testid="knowledge-group-by-project" aria-label="Group by project" />
      Group by project
    </label>
  )
}
