/**
 * The build studio's Code pane: the project tree (Plan · Backend · Tables · App) with what this build wrote marked new
 * or changed, and the file the builders are writing typing itself in. Follow (on by default) keeps the newest write
 * open; picking a file pauses it. Read-only while the build runs; once it is over, modules and app files edit in place.
 */
import { Button } from '@/shared/components/ui/button'
import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CodeXml, Crosshair } from 'lucide-react'
import { CodeTree } from '@/features/code/CodeTree'
import { FileViewer } from '@/features/code/FileViewer'
import { fileMark, mergeEntries, writerLine } from '@/features/code/codeLogic'
import { fileKeys, useProjectFiles } from '@/features/code/useFiles'
import { isActive } from '@/features/plan/buildLogic'
import type { Build } from '@/features/plan/buildTypes'
import type { LiveState } from '@/features/plan/liveBuild'
import { useAppDocumentStore } from '@/features/app-builder/stores'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { followTarget, stepOf } from './studioLogic'

export function LiveCodePane({
  pid,
  build,
  live,
  chatBusy,
  narrow,
}: {
  pid: string
  build: Build | null
  live: LiveState
  /** an app-chat turn is running: its builders may be writing (read-only until it is over) */
  chatBusy?: boolean
  narrow?: boolean
}) {
  const qc = useQueryClient()
  const buildActive = !!build && isActive(build.status)
  const building = buildActive || !!chatBusy
  const tree = useProjectFiles(pid, { refetchInterval: building ? 5000 : false })
  const [follow, setFollow] = useState(true)
  const [picked, setPicked] = useState<string | null>(null)

  // what was there before this build's writes: the first tree loaded (a file written since is new, one listed is changed)
  const [baseline, setBaseline] = useState<Set<string> | null>(null)
  if (!baseline && tree.data) setBaseline(new Set(tree.data.map((e) => e.path).filter((p) => !live.files[p])))

  const entries = useMemo(() => mergeEntries(tree.data ?? [], live), [tree.data, live])
  const writing = useMemo(() => new Set(Object.values(live.files).filter((f) => !f.done && f.op === 'write').map((f) => f.path)), [live.files])
  const newestFile = live.newest ? live.files[live.newest] : null

  // the build is over, or the app chat saved a version: the tree and the open file come from the server again
  const version = useAppDocumentStore((s) => s.currentVersion?.id ?? null)
  const status = build?.status
  useEffect(() => {
    if (!chatBusy) void qc.invalidateQueries({ queryKey: fileKeys.all(pid) })
  }, [qc, pid, status, version, chatBusy])

  const path = followTarget(follow, live.newest, picked) ?? entries.find((e) => e.path === 'PLAN.md')?.path ?? entries[0]?.path ?? null
  const lf = path ? live.files[path] : null
  const step = stepOf(build, lf?.step_key)
  const entry = entries.find((e) => e.path === path)

  return (
    <div className="flex h-full min-h-0" data-testid="studio-code">
      <div className={cn('flex shrink-0 flex-col shadow-[inset_-1px_0_0_var(--hairline)]', narrow ? 'w-48' : 'w-56')}>
        <div className="hairline-b flex h-10 shrink-0 items-center gap-2 pr-1.5 pl-3">
          <CodeXml className="size-3.5 text-hue-backend" aria-hidden />
          <span className="text-[13px] font-medium">Code</span>
          <div className="flex-1" />
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  type="button"
                  variant={follow ? 'secondary' : 'ghost'}
                  size="xs"
                  onClick={() => setFollow((f) => !f)}
                  aria-pressed={follow}
                  className={follow ? 'text-primary' : 'text-muted-foreground'}
                  data-testid="studio-follow"
                />
              }
            >
              <Crosshair className="size-3" /> Follow
            </TooltipTrigger>
            <TooltipContent>{follow ? 'Showing each file as it is written' : 'Show each file as it is written'}</TooltipContent>
          </Tooltip>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tree.isPending && !entries.length ? (
            <div className="flex flex-col gap-2 p-3">
              {[60, 80, 50, 70, 40].map((w, n) => (
                <Skeleton key={n} className="h-3.5" style={{ width: `${w}%` }} />
              ))}
            </div>
          ) : entries.length === 0 ? (
            <EmptyState icon={<CodeXml />} title="No files yet" className="px-3" data-testid="studio-code-empty">
              The plan, the backend and the app appear here as they are written.
            </EmptyState>
          ) : (
            <CodeTree
              entries={entries}
              selected={path}
              onSelect={(p) => {
                setPicked(p)
                setFollow(false)
              }}
              mark={(p) => fileMark(p, baseline, live)}
              newest={live.newest}
              newestTick={newestFile?.writes ?? 0}
              writing={writing}
              showLocks={!building}
            />
          )}
        </div>
      </div>
      <div className="@container/viewer min-w-0 flex-1">
        {path ? (
          <FileViewer
            key={path}
            pid={pid}
            path={path}
            entry={entry}
            live={lf}
            locked={buildActive ? 'The builders are writing the code; edit it once the build is done' : chatBusy ? 'The builders are changing the app; edit it once they are done' : null}
            type={building && follow && path === live.newest}
            writer={lf ? writerLine(lf.builder, step) : null}
            at={lf?.at ?? entry?.updated_at ?? null}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <span className="text-[12.5px] text-muted-foreground">Files open here.</span>
          </div>
        )}
      </div>
    </div>
  )
}
