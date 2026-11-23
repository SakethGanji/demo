/**
 * The Code place (`/p/$project/code`): every file the builders wrote, in one tree (Plan · Backend · Tables · App).
 *
 *   Files    the tree, the open file (modules and app files edit in place; the rest say how they change) and the
 *            open file's history
 *   Changes  the project's change log, newest first, filterable by build; a change opens its file
 *
 * The URL keeps the open file (`path`), the tab and the build filter, so a link reopens them.
 */
import { useMemo, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { AnimatePresence, motion } from 'motion/react'
import { CodeXml, FileClock, FilePlus2, FileMinus2, FilePen, History } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { pluralize, timeAgo } from '@/features/project/lib/format'
import { useBuilds } from '@/features/plan/useBuild'
import { FADE } from '@/features/plan/motion'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Segmented } from '@/shared/components/ui/segmented'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Tile, WellList, WellRow } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { CodeTree } from './CodeTree'
import { FileViewer } from './FileViewer'
import type { Change } from './filesTypes'
import { useChanges, useFileHistory, useProjectFiles } from './useFiles'

type Tab = 'files' | 'changes'
const ACTOR: Record<string, string> = { backend_builder: 'Backend builder', ui_builder: 'UI builder', user: 'You', plan: 'Planner' }
const OP_ICON = { create: <FilePlus2 className="text-success" />, update: <FilePen className="text-muted-foreground" />, delete: <FileMinus2 className="text-destructive" /> }

export function CodeView() {
  const { pid } = useProjectCtx()
  const search = useSearch({ from: '/p/$project/ws/code' })
  const navigate = useNavigate({ from: '/p/$project/code' })
  const files = useProjectFiles(pid)
  const entries = useMemo(() => files.data ?? [], [files.data])
  const tab: Tab = search.tab === 'changes' ? 'changes' : 'files'
  const path = search.path ?? entries.find((e) => e.path === 'PLAN.md')?.path ?? entries[0]?.path ?? null
  const entry = entries.find((e) => e.path === path)
  const [historyOpen, setHistoryOpen] = useState(true)

  const go = (s: { path?: string; tab?: 'changes'; build?: string }) => navigate({ search: (prev) => ({ ...prev, ...s }), replace: true })
  const open = (p: string) => go({ path: p, tab: undefined })

  return (
    <div className="flex h-full min-h-0 gap-2" data-testid="code-view">
      <Tile editor className="flex w-64 shrink-0 flex-col max-md:hidden">
        <div className="hairline-b flex h-10 shrink-0 items-center gap-2 px-3">
          <CodeXml className="size-3.5 text-hue-backend" aria-hidden />
          <span className="text-[13px] font-medium">Code</span>
          <span className="text-[11.5px] text-faint tabular-nums">{files.data ? pluralize(entries.length, 'file') : ''}</span>
          <div className="flex-1" />
          <Segmented<Tab>
            size="xs"
            value={tab}
            onChange={(k) => go({ tab: k === 'changes' ? 'changes' : undefined })}
            options={[
              { key: 'files', label: 'Files', testId: 'code-tab-files' },
              { key: 'changes', label: 'Changes', testId: 'code-tab-changes' },
            ]}
            aria-label="Files or changes"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {files.isPending ? (
            <div className="flex flex-col gap-2 p-3">
              {[60, 80, 50, 70, 40, 75].map((w, n) => (
                <Skeleton key={n} className="h-3.5" style={{ width: `${w}%` }} />
              ))}
            </div>
          ) : files.isError ? (
            <EmptyState title="Couldn't load the files">{errorMessage(files.error)}</EmptyState>
          ) : entries.length === 0 ? (
            <EmptyState icon={<CodeXml />} title="No files yet" data-testid="code-empty">
              Build an app from a plan and every file the builders write shows up here.
            </EmptyState>
          ) : (
            <CodeTree entries={entries} selected={tab === 'files' ? path : null} onSelect={open} showLocks />
          )}
        </div>
      </Tile>

      <AnimatePresence mode="wait" initial={false}>
        {tab === 'changes' ? (
          <motion.div key="changes" className="flex min-w-0 flex-1" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={FADE}>
            <ChangesPane pid={pid} build={search.build ?? null} onBuild={(b) => go({ build: b ?? undefined })} onOpen={open} />
          </motion.div>
        ) : (
          <motion.div key="files" className="flex min-w-0 flex-1 gap-2" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={FADE}>
            <Tile editor className="@container/viewer flex min-w-0 flex-1 flex-col overflow-hidden">
              {path ? (
                <FileViewer
                  key={path}
                  pid={pid}
                  path={path}
                  entry={entry}
                  at={entry?.updated_at ?? null}
                  actions={
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      aria-label={historyOpen ? 'Hide history' : 'Show history'}
                      aria-pressed={historyOpen}
                      onClick={() => setHistoryOpen((o) => !o)}
                      className="max-lg:hidden"
                      data-testid="code-history-toggle"
                    >
                      <History />
                    </Button>
                  }
                />
              ) : files.isPending ? null : (
                <EmptyState title="Pick a file" className="h-full">Choose a file in the tree.</EmptyState>
              )}
            </Tile>
            {path && historyOpen ? (
              <Tile editor className="flex w-72 shrink-0 flex-col max-lg:hidden">
                <HistoryPane pid={pid} path={path} />
              </Tile>
            ) : null}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ChangeLine({ c, showPath, onOpen }: { c: Change; showPath: boolean; onOpen?: () => void }) {
  const lines = c.lines_added || c.lines_removed ? (
    <span className="shrink-0 font-mono text-[11px] tabular-nums">
      <span className="text-success">+{c.lines_added}</span> <span className="text-destructive">−{c.lines_removed}</span>
    </span>
  ) : null
  const body = (
    <>
      <span className="shrink-0 [&_svg]:size-3.5" aria-hidden>
        {OP_ICON[c.op] ?? OP_ICON.update}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        {showPath ? <span className="truncate font-mono text-[12px]">{c.path}</span> : null}
        <span className={cn('truncate text-muted-foreground', showPath ? 'text-[11.5px]' : 'text-[12.5px] text-foreground')}>
          {c.summary || (c.op === 'create' ? 'Created' : c.op === 'delete' ? 'Deleted' : 'Updated')}
        </span>
        <span className="truncate text-[11px] text-faint">
          {ACTOR[c.actor] ?? c.actor} · {timeAgo(c.created_at)}
        </span>
      </span>
      {lines}
    </>
  )
  return onOpen ? (
    <WellRow className="p-0">
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-start gap-2 px-3 py-2 text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
        data-testid={`code-change-${c.id}`}
      >
        {body}
      </button>
    </WellRow>
  ) : (
    <WellRow className="items-start" data-testid={`code-history-${c.id}`}>
      {body}
    </WellRow>
  )
}

function HistoryPane({ pid, path }: { pid: string; path: string }) {
  const history = useFileHistory(pid, path)
  const rows = history.data ?? []
  return (
    <>
      <div className="hairline-b flex h-10 shrink-0 items-center gap-2 px-3">
        <FileClock className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="text-[13px] font-medium">History</span>
        <span className="text-[11.5px] text-faint tabular-nums">{history.data ? rows.length : ''}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2" data-testid="code-history">
        {history.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : history.isError ? (
          <EmptyState title="Couldn't load the history">{errorMessage(history.error)}</EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState title="No changes recorded">Changes from builds and your saves show up here.</EmptyState>
        ) : (
          <WellList>
            {rows.map((c) => (
              <ChangeLine key={c.id} c={c} showPath={false} />
            ))}
          </WellList>
        )}
      </div>
    </>
  )
}

function ChangesPane({ pid, build, onBuild, onOpen }: { pid: string; build: string | null; onBuild: (b: string | null) => void; onOpen: (p: string) => void }) {
  const changes = useChanges(pid, build)
  const builds = useBuilds(pid)
  const rows = changes.data ?? []
  const ALL = '__all'
  const items: Record<string, string> = { [ALL]: 'All builds' }
  for (const b of builds.data ?? []) items[b.id] = `Build · ${timeAgo(b.created_at)} · ${b.status}`
  return (
    <Tile editor className="flex min-w-0 flex-1 flex-col" data-testid="code-changes">
      <div className="hairline-b flex h-10 shrink-0 items-center gap-2 pr-2 pl-3">
        <History className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="text-[13px] font-medium">Changes</span>
        <span className="text-[11.5px] text-faint tabular-nums">{changes.data ? rows.length : ''}</span>
        <div className="flex-1" />
        <Select value={build ?? ALL} onValueChange={(v) => onBuild(!v || v === ALL ? null : (v as string))} items={items}>
          <SelectTrigger size="sm" className="w-56" data-testid="code-changes-build">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(items).map(([k, v]) => (
              <SelectItem key={k} value={k}>
                {v}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {changes.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : changes.isError ? (
          <EmptyState title="Couldn't load the changes">{errorMessage(changes.error)}</EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState icon={<History />} title={build ? 'This build changed no files' : 'No changes yet'} data-testid="code-changes-empty">
            Every file a builder writes, and every save you make, is logged here.
          </EmptyState>
        ) : (
          <WellList>
            {rows.map((c) => (
              <ChangeLine key={c.id} c={c} showPath onOpen={c.op === 'delete' ? undefined : () => onOpen(c.path)} />
            ))}
          </WellList>
        )}
      </div>
    </Tile>
  )
}
