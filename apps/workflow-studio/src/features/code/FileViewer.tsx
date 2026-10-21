/**
 * One file of the project tree, viewed or edited.
 *
 *   during a build  the content written this build (`live`), read-only; the newest write types itself in (a chunked
 *                   reveal, about a second for any file; a click shows it all), with who wrote it and when
 *   otherwise       the file as the files API has it; writable kinds (modules, app files) edit in place and save with
 *                   PUT and the `updated_at` they were read at: a read-only kind says how to change it instead, and a
 *                   newer save offers Reload or Save anyway
 *
 * Mount it with `key={path}` so each file starts clean.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { AnimatePresence, motion } from 'motion/react'
import { EditorView } from '@codemirror/view'
import { ArrowRight, FileCode2, Lock, RotateCw, Save } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useNow } from '@/features/plan/useBuild'
import { FADE, useCalm } from '@/features/plan/motion'
import type { LiveFile } from '@/features/plan/liveBuild'
import { Shimmer } from '@/shared/components/ai-elements/shimmer'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import CodeEditor from '@/shared/components/ui/code-editor'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Kbd } from '@/shared/components/ui/kbd'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Callout } from '@/shared/components/ui/surface'
import { errorMessage } from '@/shared/api/http'
import { cn } from '@/shared/lib/utils'
import { commonPrefix, editorLanguage, relativeTime, revealChunk, saveProblem, type SaveProblem } from './codeLogic'
import type { FileEntry } from './filesTypes'
import { useFileContent, useSaveFile } from './useFiles'

export interface FileViewerProps {
  pid: string
  path: string
  entry?: FileEntry
  /** what the running build wrote to this path */
  live?: LiveFile | null
  /** while set the file is read-only and says why (the build is running) */
  locked?: string | null
  /** type the live content in */
  type?: boolean
  /** "Backend builder · Runs module" */
  writer?: string | null
  /** when it was written (ms or ISO) */
  at?: number | string | null
  actions?: ReactNode
  className?: string
}

function ReadOnlyWhy({ kind, path }: { kind: FileEntry['kind']; path: string }) {
  const { slug } = useProjectCtx()
  const link = 'inline-flex items-center gap-0.5 font-medium text-primary hover:underline underline-offset-2'
  if (kind === 'plan')
    return (
      <>
        The plan changes through a revision.{' '}
        <Link to="/p/$project/plan" params={{ project: slug }} className={link}>
          Open the plan <ArrowRight className="size-3" />
        </Link>
      </>
    )
  if (kind === 'table') {
    const table = path.split('/').pop()?.replace(/\.json$/, '') ?? ''
    return (
      <>
        A table changes through a migration in its module.{' '}
        <Link to="/p/$project/data/$table" params={{ project: slug, table }} className={link}>
          Open the data <ArrowRight className="size-3" />
        </Link>
      </>
    )
  }
  return <>Routes come from the modules' code: edit a module to change them.</>
}

export function FileViewer({ pid, path, entry, live, locked, type, writer, at, actions, className }: FileViewerProps) {
  const calm = useCalm()
  const building = !!locked
  const useLive = building && !!live && live.op === 'write'
  const fetched = useFileContent(pid, path, !useLive)
  const save = useSaveFile(pid)
  const target = useLive ? (live?.content ?? '') : (fetched.data?.content ?? '')
  const [shown, setShown] = useState('')
  const [skipped, setSkipped] = useState(false)
  const [draft, setDraft] = useState<string | null>(null)
  const [problem, setProblem] = useState<SaveProblem | null>(null)
  const viewRef = useRef<EditorView | null>(null)
  const shownRef = useRef(shown)
  const now = useNow(at != null)
  // typing only for the live file the build is writing (never when calm, never after a click)
  const animated = !!type && !calm && !skipped
  const typing = animated && shown !== target

  useEffect(() => {
    shownRef.current = shown
  }, [shown])

  // the typing reveal: from what is already on screen (a draft rewritten keeps its common start) to the new content
  useEffect(() => {
    if (!animated) return
    let i = Math.min(commonPrefix(shownRef.current, target), target.length)
    const step = revealChunk(target.length - i)
    let raf = 0
    const tick = () => {
      i = Math.min(target.length, i + step)
      setShown(target.slice(0, i))
      if (i < target.length) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, animated])

  // keep the newest line in view while it types
  useEffect(() => {
    const v = viewRef.current
    if (v && typing) v.dispatch({ effects: EditorView.scrollIntoView(v.state.doc.length, { y: 'end' }) })
  }, [shown, typing])

  const skip = () => {
    if (typing) setSkipped(true)
  }

  const kind = entry?.kind ?? fetched.data?.kind
  const writable = !building && !!(fetched.data?.writable ?? entry?.writable) && !useLive
  const value = draft ?? (animated ? shown : target)
  const dirty = draft != null && draft !== fetched.data?.content

  const doSave = (base: string | null | undefined) => {
    if (draft == null || !fetched.data) return
    setProblem(null)
    save.mutate(
      { path, content: draft, base_updated_at: base ?? undefined, entry: entry ?? { kind: fetched.data.kind, ref: { type: fetched.data.kind, id: path } } },
      {
        onSuccess: () => {
          setDraft(null)
          toast.success(`Saved ${path.split('/').pop()}`)
        },
        onError: (e) => setProblem(saveProblem(e)),
      },
    )
  }
  const reload = () => {
    setDraft(null)
    setProblem(null)
    void fetched.refetch()
  }

  const name = path.split('/').pop() ?? path
  const dir = path.slice(0, path.length - name.length)
  const truncated = useLive && live?.truncated

  return (
    <div
      className={cn('@container/viewer flex h-full min-h-0 flex-col', className)}
      data-testid="file-viewer"
      data-path={path}
      data-typing={typing || undefined}
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
          e.preventDefault()
          if (dirty) doSave(fetched.data?.updated_at)
        }
      }}
    >
      <div className="hairline-b flex h-10 shrink-0 items-center gap-2 pl-3 pr-2">
        <FileCode2 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 truncate font-mono text-[12px]" title={path}>
          <span className="text-muted-foreground">{dir}</span>
          {name}
        </span>
        {writer || at != null ? (
          <span className="hidden min-w-0 truncate text-[11.5px] text-muted-foreground @min-[520px]/viewer:inline" data-testid="file-writer">
            {writer}
            {writer && at != null ? ' · ' : ''}
            {at != null ? relativeTime(at, now) : ''}
          </span>
        ) : null}
        <div className="flex-1" />
        {useLive && live && !live.done ? (
          <Shimmer as="span" className="shrink-0 text-[11.5px] font-medium" duration={1.4}>
            writing…
          </Shimmer>
        ) : null}
        {building ? (
          <Badge variant="neutral" className="shrink-0" title={locked ?? undefined}>
            <Lock /> read-only
          </Badge>
        ) : !writable && fetched.data ? (
          <Badge variant="neutral" className="shrink-0">
            <Lock /> read-only
          </Badge>
        ) : null}
        {writable && dirty ? (
          <Button size="xs" onClick={() => doSave(fetched.data?.updated_at)} disabled={save.isPending} data-testid="file-save">
            {save.isPending ? <Spinner /> : <Save />} Save <Kbd className="ml-0.5">⌘S</Kbd>
          </Button>
        ) : null}
        {actions}
      </div>
      {problem ? (
        <div className="shrink-0 px-2 pt-2">
          {problem.kind === 'conflict' ? (
            <Callout tone="warn" data-testid="file-conflict">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">{problem.message}</span>
                <Button size="xs" variant="outline" onClick={reload} data-testid="file-reload">
                  <RotateCw /> Reload
                </Button>
                <Button size="xs" variant="ghost" onClick={() => doSave(problem.updated_at)} data-testid="file-overwrite">
                  Save anyway
                </Button>
              </div>
            </Callout>
          ) : (
            <Callout tone={problem.kind === 'read_only' ? 'info' : 'error'} data-testid={problem.kind === 'read_only' ? 'file-read-only' : 'file-save-error'}>
              {problem.message}
            </Callout>
          )}
        </div>
      ) : !building && fetched.data && !fetched.data.writable && kind ? (
        <div className="shrink-0 px-2 pt-2">
          <Callout tone="info" icon={<Lock />} data-testid="file-read-only">
            <ReadOnlyWhy kind={kind} path={path} />
          </Callout>
        </div>
      ) : null}
      {truncated ? (
        <div className="shrink-0 px-2 pt-2">
          <Callout tone="warn">Showing the first 200 KB of this file.</Callout>
        </div>
      ) : null}
      <div className="relative min-h-0 flex-1" onClick={skip}>
        {!useLive && fetched.isPending ? (
          <div className="flex flex-col gap-2 p-4">
            {[72, 56, 88, 40, 64].map((w, n) => (
              <Skeleton key={n} className="h-3" style={{ width: `${w}%` }} />
            ))}
          </div>
        ) : !useLive && fetched.isError ? (
          <EmptyState title="Couldn't open this file" className="h-full">{errorMessage(fetched.error)}</EmptyState>
        ) : (
          <CodeEditor
            value={value}
            onChange={writable ? (v) => setDraft(v === fetched.data?.content ? null : v) : undefined}
            language={editorLanguage(path)}
            readOnly={!writable}
            height="100%"
            bare
            wrap={false}
            onReady={(v) => {
              viewRef.current = v
            }}
            testId="file-editor"
          />
        )}
        <AnimatePresence>
          {typing ? (
            <motion.button
              type="button"
              initial={{ opacity: 0, y: calm ? 0 : 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={FADE}
              onClick={skip}
              className="glass-chrome absolute right-3 bottom-3 rounded-full px-2.5 py-1 text-[11.5px] text-muted-foreground hover:text-foreground"
              data-testid="file-skip"
            >
              Click to show it all
            </motion.button>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  )
}
