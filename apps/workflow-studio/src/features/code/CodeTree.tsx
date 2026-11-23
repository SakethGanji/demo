/**
 * The project file tree, grouped Plan · Backend · Tables · App. Files written by a running build carry a dot (new or
 * changed); the newest write glows; a module still being drafted pulses. New rows pop in (height and opacity) as the
 * builders write them; the tree already on screen when it mounts does not animate.
 */
import { useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { AppWindow, Braces, ChevronRight, ClipboardList, Database, FileCode2, FileText, Lock, Server, Table2 } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { SPRING, useCalm } from '@/features/plan/motion'
import { treeRows, type GroupKey, type TreeRow } from './codeLogic'
import type { FileEntry } from './filesTypes'

const GROUP_ICON: Record<GroupKey, { icon: ReactNode; text: string }> = {
  plan: { icon: <ClipboardList />, text: 'text-hue-flow' },
  backend: { icon: <Server />, text: 'text-hue-backend' },
  tables: { icon: <Database />, text: 'text-hue-data' },
  app: { icon: <AppWindow />, text: 'text-hue-apps' },
}

function fileIcon(path: string, group: GroupKey): ReactNode {
  if (group === 'tables') return <Table2 />
  const ext = path.split('.').pop()?.toLowerCase()
  if (ext === 'json') return <Braces />
  if (ext === 'md') return <FileText />
  return <FileCode2 />
}

export interface CodeTreeProps {
  entries: FileEntry[]
  selected: string | null
  onSelect: (path: string) => void
  /** the new / changed dot of a file written this build */
  mark?: (path: string) => 'new' | 'changed' | null
  /** the latest write (glows); `newestTick` re-flashes it on each write */
  newest?: string | null
  newestTick?: number
  /** paths still being drafted */
  writing?: Set<string>
  /** a lock on read-only files */
  showLocks?: boolean
  className?: string
}

const ROW = 26

export function CodeTree({ entries, selected, onSelect, mark, newest, newestTick, writing, showLocks, className }: CodeTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const rows = useMemo(() => treeRows(entries, collapsed), [entries, collapsed])
  const calm = useCalm()
  const toggle = (path: string) =>
    setCollapsed((c) => {
      const n = new Set(c)
      if (n.has(path)) n.delete(path)
      else n.add(path)
      return n
    })
  const key = (r: TreeRow) => (r.type === 'group' ? `g:${r.key}` : `${r.type}:${r.path}`)

  return (
    <div className={cn('flex flex-col py-1.5', className)} role="tree" aria-label="Project files" data-testid="code-tree">
      <AnimatePresence initial={false}>
        {rows.map((r) => (
          <motion.div
            key={key(r)}
            layout={calm ? false : 'position'}
            initial={calm ? { opacity: 0 } : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: r.type === 'group' ? 'auto' : ROW }}
            exit={calm ? { opacity: 0 } : { opacity: 0, height: 0 }}
            transition={calm ? { duration: 0.15 } : SPRING}
            className="shrink-0 overflow-hidden"
          >
            {r.type === 'group' ? (
              <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1 text-[11.5px] font-medium text-muted-foreground" data-testid={`code-group-${r.key}`}>
                <span className={cn('[&_svg]:size-3 [&_svg]:stroke-[2]', GROUP_ICON[r.key].text)} aria-hidden>
                  {GROUP_ICON[r.key].icon}
                </span>
                {r.label}
                <span className="text-faint tabular-nums">{r.count}</span>
              </div>
            ) : r.type === 'dir' ? (
              <button
                type="button"
                onClick={() => toggle(r.path)}
                className="flex h-[26px] w-full items-center gap-1 rounded-ctl pr-2 text-left text-[12.5px] text-foreground/80 outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
                style={{ paddingLeft: 10 + r.depth * 12 }}
                aria-expanded={!collapsed.has(r.path)}
                data-testid={`code-dir-${r.path}`}
              >
                <ChevronRight className={cn('size-3 shrink-0 text-faint transition-transform duration-(--d-fast)', !collapsed.has(r.path) && 'rotate-90')} />
                <span className="truncate">{r.name}</span>
              </button>
            ) : (
              <FileRow
                row={r}
                selected={selected === r.path}
                mark={mark?.(r.path) ?? null}
                newest={newest === r.path}
                tick={newestTick ?? 0}
                writing={!!writing?.has(r.path)}
                lock={!!showLocks && !r.entry.writable}
                onSelect={onSelect}
                calm={calm}
              />
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}

function FileRow({
  row,
  selected,
  mark,
  newest,
  tick,
  writing,
  lock,
  onSelect,
  calm,
}: {
  row: Extract<TreeRow, { type: 'file' }>
  selected: boolean
  mark: 'new' | 'changed' | null
  newest: boolean
  tick: number
  writing: boolean
  lock: boolean
  onSelect: (p: string) => void
  calm: boolean
}) {
  return (
    <button
      type="button"
      role="treeitem"
      aria-selected={selected}
      onClick={() => onSelect(row.path)}
      className={cn(
        'relative flex h-[26px] w-full items-center gap-1.5 rounded-ctl pr-2 text-left text-[12.5px] outline-none focus-visible:shadow-focus',
        selected ? 'bg-foreground/7 text-foreground' : 'text-foreground/80 hover:bg-(--hover-fill)',
      )}
      style={{ paddingLeft: 10 + row.depth * 12 + 14 }}
      title={row.path}
      data-testid={`code-file-${row.path}`}
      data-mark={mark ?? undefined}
      data-newest={newest || undefined}
    >
      {newest ? (
        // the newest write: a glow that flashes on each write, then settles to a faint tint
        <motion.span
          key={tick}
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-ctl bg-primary/10"
          initial={{ opacity: calm ? 0.6 : 1 }}
          animate={{ opacity: 0.55 }}
          transition={{ duration: calm ? 0.15 : 0.9, ease: [0.16, 1, 0.3, 1] }}
        />
      ) : null}
      <span className="relative shrink-0 text-muted-foreground [&_svg]:size-3.5 [&_svg]:stroke-[1.75]" aria-hidden>
        {fileIcon(row.path, row.group)}
      </span>
      <span className={cn('relative min-w-0 flex-1 truncate', row.group !== 'plan' && row.name.includes('.') && 'font-mono text-[11.5px]')}>{row.name}</span>
      {lock ? <Lock className="relative size-3 shrink-0 text-faint" aria-label="read-only" /> : null}
      {writing ? (
        <span className="relative size-1.5 shrink-0 animate-pulse rounded-full bg-primary" aria-label="being written" />
      ) : mark ? (
        <motion.span
          initial={calm ? { opacity: 0 } : { scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={calm ? { duration: 0.15 } : SPRING}
          className={cn('relative size-1.5 shrink-0 rounded-full', mark === 'new' ? 'bg-success' : 'bg-warning')}
          aria-label={mark === 'new' ? 'new' : 'changed'}
          title={mark === 'new' ? 'New in this build' : 'Changed in this build'}
        />
      ) : null}
    </button>
  )
}
