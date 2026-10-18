/**
 * Backend view (UI-DESIGN §4, `backend.png`, `states.png`):
 * `[editor panel (file bar · composer · code · status bar)] [inspector: Test · Reference · client.ts]`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { EditorView, keymap } from '@codemirror/view'
import { Text, Prec, type Extension } from '@codemirror/state'
import { toggleComment } from '@codemirror/commands'
import { lintGutter } from '@codemirror/lint'
import { BookOpen, CircleCheck, CircleX, Code2, Ellipsis, FileCode2, FileType2, KeyRound, Play, ShieldCheck, ShieldAlert, TriangleAlert, Wand2 } from 'lucide-react'
import { toast } from 'sonner'
import CodeEditor, { type CodeEditorHandle } from '@/shared/components/ui/code-editor'
import { Button } from '@/shared/components/ui/button'
import { Kbd } from '@/shared/components/ui/kbd'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/shared/components/ui/tabs'
import { Segmented } from '@/shared/components/ui/segmented'
import { Roll } from '@/shared/components/ui/roll'
import { Callout, Tile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { Popover, PopoverContent } from '@/shared/components/ui/popover'
import { ChipsInput } from '@/shared/components/ui/chips-input'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useModules, useRoutes, useSandboxStatus, useSdkFunctions, useSdkReference, useSecrets, useTables } from '@/features/project/hooks/useProjectData'
import { CreateSecretForm } from '@/features/secrets/CreateSecretPopover'
import { useUpdateSecret } from '@/features/secrets/useSecretMutations'
import { CodeMapPanel } from './code-map/CodeMapPanel'
import { modulesApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { PlannedRoute, RunDraftResult, SelfTestResult } from '@/shared/api/types'
import { timeAgo } from '@/features/project/lib/format'
import { useViewportWidth } from '@/shared/hooks/useViewport'
import { cn } from '@/shared/lib/utils'
import { isDirty, loadPersisted, useDocStore, docKey, clearPersisted, type ModuleDoc, type RecentTest } from './docStore'
import { problemsToDiagnostics, routesOf, useLiveAnalysis, type Problem } from './useAnalysis'
import { useSaveModule } from './useSaveModule'
import { useGenerate } from './useGenerate'
import { useOutlineDraftStore } from './outlineDraftStore'
import { lineDiff } from './cm/lineDiff'
import { lineTints, scanSecretRefs, secretWidgets, type SecretIssue } from './cm/decorations'
import { studioAutocomplete } from './cm/completions'
import { PromptComposer, type ComposerHandle } from './components/PromptComposer'
import { TestPanel, type TestRoute } from './components/TestPanel'
import { ReferencePanel } from './components/ReferencePanel'
import { ClientTsPanel } from './components/ClientTsPanel'
import { BreakingChangesDialog, DeleteModuleDialog, ModuleChangedDialog, RenameModuleDialog } from './components/ModuleDialogs'
import { DiffDialog } from './components/DiffDialog'
import { templateSource, TEMPLATE_LABELS, type TemplateKey } from './templates'
import { STARTERS } from '@/features/project/components/SetupGuide'
import type { BackendSearch } from '@/app/routes/project'

export function BackendView() {
  const { pid } = useProjectCtx()
  const { module } = useParams({ strict: false }) as { module: string }
  const modules = useModules(pid)
  const saved = modules.data?.find((m) => m.name === module) ?? null
  const doc = useDocStore((s) => s.docs[docKey(pid, module)])
  const put = useDocStore((s) => s.put)
  const openTab = useDocStore((s) => s.openTab)
  const detail = useQuery({
    queryKey: qk.module(pid, saved?.id ?? ''),
    queryFn: () => modulesApi.get(pid, saved!.id),
    enabled: !!saved,
  })

  // Initialise the document once: from sessionStorage (draft survives reload), else the saved source.
  const renamedTo = useDocStore((s) => s.renamed[docKey(pid, module)])
  const clearRenamed = useDocStore((s) => s.clearRenamed)
  // the router reached the new name: the rename marker (keyed by the OLD name) has done its job
  const renamedMap = useDocStore((s) => s.renamed)
  useEffect(() => {
    for (const [k, to] of Object.entries(renamedMap)) if (k.startsWith(`${pid}:`) && to === module) clearRenamed(pid, k.slice(pid.length + 1))
  }, [renamedMap, module, pid, clearRenamed])
  useEffect(() => {
    if (doc) return
    if (renamedTo) return // renamed (e.g. ~new → refunds) and the router has not navigated yet: don't re-create the old doc
    if (module === '~new') {
      const p = loadPersisted(pid, '~new')
      put({
        projectId: pid,
        module: '~new',
        moduleId: null,
        source: p?.source ?? '',
        savedSource: '',
        baseSha: null,
        isNew: true,
        draft: p?.draft ?? null,
        prompts: p?.prompts ?? [],
        recentTests: [],
        editorKey: p?.editorKey ?? `new-${Date.now()}`,
      })
      return
    }
    if (!modules.data) return
    const p = loadPersisted(pid, module)
    if (saved) {
      if (!detail.data) return
      const savedSource = detail.data.source ?? ''
      if (p && !p.isNew) {
        put({
          projectId: pid,
          module,
          moduleId: saved.id,
          source: p.source,
          savedSource,
          baseSha: p.baseSha,
          isNew: false,
          draft: p.draft,
          prompts: p.prompts,
          recentTests: [],
          staleRestore: !!p.baseSha && p.baseSha !== detail.data.source_sha,
          editorKey: p.editorKey ?? `${module}-${Date.now()}`,
        })
      } else {
        put({
          projectId: pid,
          module,
          moduleId: saved.id,
          source: savedSource,
          savedSource,
          baseSha: detail.data.source_sha,
          isNew: false,
          draft: null,
          prompts: [],
          recentTests: [],
          editorKey: `${module}-${Date.now()}`,
        })
      }
      openTab(pid, module)
    } else if (p?.isNew) {
      put({ projectId: pid, module, moduleId: null, source: p.source, savedSource: '', baseSha: null, isNew: true, draft: p.draft, prompts: p.prompts, recentTests: [], editorKey: p.editorKey ?? `${module}-${Date.now()}` })
      openTab(pid, module)
    }
  }, [doc, module, modules.data, saved, detail.data, pid, put, openTab])

  if (modules.isLoading || (saved && detail.isLoading && !doc)) return <BackendSkeleton />
  if (saved && detail.isError && !doc)
    return (
      <Tile editor className="flex h-full items-center justify-center" data-testid="backend-load-error">
        <EmptyState
          title={`Couldn't load ${module}.py`}
          actions={
            <Button variant="outline" size="sm" onClick={() => detail.refetch()}>
              Retry
            </Button>
          }
        >
          {errorMessage(detail.error)}
        </EmptyState>
      </Tile>
    )
  if (!doc) {
    if (modules.data && !saved && module !== '~new')
      return (
        <Tile editor className="flex h-full items-center justify-center" data-testid="module-not-found">
          <EmptyState title={`There is no module called ${module}.py`}>It may have been renamed or deleted.</EmptyState>
        </Tile>
      )
    return <BackendSkeleton />
  }
  return <BackendWorkspace doc={doc} savedUpdatedAt={detail.data?.updated_at ?? saved?.updated_at ?? null} />
}

function BackendSkeleton() {
  return (
    <div className="flex h-full gap-2" data-testid="backend-loading">
      <Tile editor className="flex-[1.15] p-4">
        {[70, 40, 0, 85, 60, 0, 75, 50, 65].map((w, i) => (
          <Skeleton key={i} className="mb-2.5 h-4" style={{ width: `${w}%`, opacity: w ? 1 : 0 }} />
        ))}
      </Tile>
      <Tile editor className="flex-1 p-4">
        <Skeleton className="h-8" />
      </Tile>
    </div>
  )
}

function selfTestToRecent(r: SelfTestResult, i: number): RecentTest | null {
  if (r.skipped || r.status == null) return null
  const [method, ...rest] = r.route.split(' ')
  let body: unknown = r.body_preview ?? null
  try {
    if (typeof r.body_preview === 'string') body = JSON.parse(r.body_preview)
  } catch {
    /* keep text */
  }
  const result: RunDraftResult = {
    status: r.status,
    headers: {},
    body,
    duration_ms: r.duration_ms ?? 0,
    execution_id: r.execution_id ?? null,
    route: { method, path: rest.join(' '), name: '', function: '', line: 0 },
    trace: null,
    error: r.error_code ? { type: 'SelfTest', message: r.error_code, code: r.error_code } : null,
  }
  return {
    id: `selftest-${Date.now()}-${i}`,
    at: Date.now(),
    method,
    path: rest.join(' '),
    routeName: '',
    request: { params: {}, query: {}, headers: {}, body: '' },
    result,
    draft: true,
  }
}

function BackendWorkspace({ doc, savedUpdatedAt }: { doc: ModuleDoc; savedUpdatedAt: string | null }) {
  const { project, pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const search = useSearch({ strict: false }) as BackendSearch
  const width = useViewportWidth()
  const narrow = width < 1360
  const veryNarrow = width < 1280

  const patch = useDocStore((s) => s.patch)
  const renameDoc = useDocStore((s) => s.rename)
  const removeDoc = useDocStore((s) => s.remove)
  const setOutline = useOutlineDraftStore((s) => s.set)
  const clearOutline = useOutlineDraftStore((s) => s.clear)

  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const tables = useTables(pid)
  const secrets = useSecrets(pid)
  const sandbox = useSandboxStatus()
  const fns = useSdkFunctions()
  const ref = useSdkReference()

  const editorRef = useRef<CodeEditorHandle>(null)
  const composerRef = useRef<ComposerHandle>(null)
  const [cursor, setCursor] = useState({ line: 1, col: 1 })
  const [inspectorTab, setInspectorTab] = useState<'test' | 'reference' | 'client'>('test')
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [editorMode, setEditorMode] = useState<'code' | 'visual' | 'split'>('code')
  const [selectedRoute, setSelectedRoute] = useState<string | null>(search.route ?? null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [fix, setFix] = useState<{ issue: SecretIssue; anchor: HTMLElement } | null>(null)
  const [diffOpen, setDiffOpen] = useState<null | 'draft' | 'theirs'>(null)
  const [theirs, setTheirs] = useState<string | null>(null)
  const [dialog, setDialog] = useState<null | 'delete' | 'rename'>(null)
  const [busy, setBusy] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)

  const existingTables = useMemo(() => (tables.data ?? []).map((t) => t.name), [tables.data])
  const saver = useSaveModule(pid, existingTables)
  const gen = useGenerate(pid)
  const streaming = gen.state.kind === 'streaming'
  const dirty = isDirty(doc) || !!doc.draft

  // ── live analysis ──
  const analysis = useLiveAnalysis({
    pid,
    moduleId: doc.moduleId,
    moduleName: doc.module,
    source: doc.source,
    enabled: !streaming && !saver.saving,
    skipSource: doc.isNew ? null : doc.savedSource,
  })

  const problems: Problem[] = useMemo(() => {
    if (saver.problems && saver.problems.source === doc.source) return saver.problems.problems
    if (analysis.source === doc.source && analysis.problems.length) return analysis.problems
    if (doc.draft?.invalid && doc.draft.analysis)
      return doc.draft.analysis.errors.map((e) => ({ code: e.code, line: e.line, column: e.column ?? null, message: e.message }))
    return []
  }, [saver.problems, analysis.source, analysis.problems, doc.source, doc.draft])

  const diagnostics = useMemo(() => problemsToDiagnostics(Text.of(doc.source.split('\n')), problems), [doc.source, problems])

  // ── routes for the Test panel ──
  const savedRoutes = useMemo(
    () => (routes.data ?? []).filter((r) => r.handler.type === 'python' && r.handler.module_id === doc.moduleId),
    [routes.data, doc.moduleId],
  )
  const planned: PlannedRoute[] = useMemo(() => {
    if (!dirty) return []
    if (analysis.result && analysis.source === doc.source) return routesOf(analysis.result)
    if (doc.draft?.analysis) return routesOf({ routes: doc.draft.analysis.routes } as never)
    return []
  }, [dirty, analysis.result, analysis.source, doc.source, doc.draft])
  const testRoutes: TestRoute[] = useMemo(() => {
    if (!dirty)
      return savedRoutes.map((r) => ({ method: r.method, path: r.path, name: r.name, draft: false, routeId: r.id, auth: r.auth }))
    const base = planned.length ? planned : savedRoutes
    return base.map((r) => {
      const s = savedRoutes.find((x) => x.method === r.method && x.path === r.path)
      return { method: r.method, path: r.path, name: r.name, draft: !s, routeId: s?.id ?? null, auth: (r as { auth?: string }).auth ?? s?.auth }
    })
  }, [dirty, planned, savedRoutes])
  const newRoutes = useMemo(() => planned.filter((r) => !savedRoutes.some((s) => s.method === r.method && s.path === r.path)), [planned, savedRoutes])

  useEffect(() => {
    if (search.route) setSelectedRoute(search.route)
  }, [search.route])

  // ── draft tables ──
  const draftTables = doc.draft?.tables ?? []
  const virtualTables = draftTables.filter((t) => !existingTables.includes(t.name)).map((t) => t.name)
  const draftColumns = Object.fromEntries(draftTables.map((t) => [t.name, t.columns]))

  // ── secret widgets (§4.5) ──
  const envs = project.environments.map((e) => e.name)
  const secretIssues: SecretIssue[] = useMemo(() => {
    const keys = [...new Set(scanSecretRefs(doc.source).map((r) => r.key))]
    const fromAnalysis = doc.draft?.analysis?.secrets
    const hostsInCode = [...doc.source.matchAll(/https?:\/\/([A-Za-z0-9.-]+(?::\d+)?)/g)].map((m) => m[1])
    const out: SecretIssue[] = []
    for (const key of keys) {
      let presentIn: string[]
      let bound = true
      // the generation-time analysis is only a stand-in until the live secrets load: after that the live list is
      // fresher (a secret fixed from the widget must clear it)
      const a = secrets.data ? undefined : fromAnalysis?.find((s) => s.key === key)
      if (a) {
        presentIn = a.present_in
        bound = a.bound
      } else {
        const rows = (secrets.data ?? []).filter((s) => s.key === key)
        presentIn = [...new Set(rows.map((s) => s.environment))].filter((e) => envs.includes(e))
        const proj = rows.filter((s) => s.scope === 'project')
        bound = proj.length === 0 || proj.every((s) => s.allowed_hosts && s.allowed_hosts.length > 0)
      }
      const missingIn = envs.filter((e) => !presentIn.includes(e))
      if (presentIn.length === 0) out.push({ key, kind: 'missing_everywhere', presentIn, missingIn })
      else if (missingIn.length) out.push({ key, kind: 'missing_in', presentIn, missingIn })
      else if (!bound) out.push({ key, kind: 'not_bound', presentIn, missingIn, host: hostsInCode[0] ?? null })
    }
    return out
  }, [doc.source, doc.draft, secrets.data, envs])
  const visibleIssues = useMemo(() => secretIssues.filter((i) => !dismissed.has(i.key)), [secretIssues, dismissed])
  const missingSecretKeys = secretIssues.filter((i) => i.kind !== 'not_bound').map((i) => i.key)
  const missingEnvs = [...new Set(secretIssues.flatMap((i) => i.missingIn))]

  // publish what the draft would change for the outline (§3.1)
  useEffect(() => {
    setOutline({
      projectId: pid,
      module: doc.module,
      isNew: doc.isNew,
      dirty,
      newRoutes,
      tablesOnSave: draftTables.filter((t) => t.create).map((t) => t.name),
      missingSecretKeys,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, doc.module, doc.isNew, dirty, JSON.stringify(newRoutes), JSON.stringify(draftTables), missingSecretKeys.join(',')])
  useEffect(() => () => {
    const d = useDocStore.getState().docs[docKey(pid, doc.module)]
    if (!d || (!isDirty(d) && !d.draft)) clearOutline(pid, doc.module)
  }, [pid, doc.module, clearOutline])

  // ── diff tint for a pending draft ──
  const added = useMemo(() => (doc.draft ? lineDiff(doc.draft.previousSource, doc.source).added : []), [doc.draft, doc.source])
  const diffStat = useMemo(() => {
    if (!doc.draft) return null
    const d = lineDiff(doc.draft.previousSource, doc.source)
    return { added: d.addedCount, removed: d.removedCount }
  }, [doc.draft, doc.source])

  // ── actions ──
  const saveRef = useRef<() => void>(() => undefined)
  const doSave = useCallback(
    async (opts: { confirmedBreaking?: boolean; overwrite?: boolean } = {}) => {
      if (streaming) return
      const cur = useDocStore.getState().docs[docKey(pid, doc.module)]
      if (!cur) return
      if (cur.module === '~new') {
        toast.error('Name the module first', { description: 'Generate a module, or use New module to start one by hand.' })
        return
      }
      analysis.cancel()
      const r = await saver.save(cur, opts)
      if (r.ok) {
        clearOutline(pid, cur.module)
      }
    },
    [pid, doc.module, saver, streaming, clearOutline, analysis.cancel],
  )
  saveRef.current = () => void doSave()

  const goToLine = useCallback((line: number) => {
    const view = editorRef.current?.view
    if (!view) return
    const l = view.state.doc.line(Math.max(1, Math.min(line, view.state.doc.lines)))
    view.dispatch({ selection: { anchor: l.from }, effects: EditorView.scrollIntoView(l.from, { y: 'center' }) })
    view.focus()
  }, [])

  // ?line=N (from run sheet / secrets "Referenced by")
  useEffect(() => {
    if (search.line == null) return
    const t = setTimeout(() => {
      goToLine(search.line!)
      navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, line: undefined }), replace: true } as never)
    }, 50)
    return () => clearTimeout(t)
  }, [search.line, goToLine, navigate])

  // ── generation ──
  const runGenerate = useCallback(
    async (prompt: string) => {
      const cur = useDocStore.getState().docs[docKey(pid, doc.module)]
      if (!cur) return
      const before = cur.source
      const conversation = cur.prompts.slice(-6)
      const out = await gen.generate(
        {
          prompt,
          module_id: cur.moduleId,
          module_name: cur.module === '~new' ? null : cur.module,
          current_source: before || null,
          conversation,
          self_test: true,
        },
      )
      if (!out) return
      const now = useDocStore.getState().docs[docKey(pid, doc.module)] ?? cur
      const isResult = 'result' in out
      const draftSource = isResult ? out.result.source : out.invalid.draft.source
      const moduleName = isResult ? out.result.module_name : out.invalid.draft.module_name
      const an = isResult ? out.result.analysis : out.invalid.analysis
      const declared = (isResult ? out.result.tables : undefined) ?? []
      const tablesNeeded = (an?.tables ?? [])
        .filter((t) => !t.exists)
        .map((t) => ({ name: t.name, columns: t.columns ?? declared.find((d) => d.name === t.name)?.columns ?? [], create: true }))
      // One transaction from the pre-draft text, so ⌘Z restores it in one step (§4.2 step 3).
      const view = editorRef.current?.view
      if (view) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: draftSource }, userEvent: 'ai.draft' })
      const selfTest = isResult ? out.result.self_test : null
      const recent = (selfTest ?? []).map(selfTestToRecent).filter((x): x is RecentTest => !!x)
      patch(pid, now.module, {
        source: draftSource,
        draft: {
          previousSource: now.draft?.previousSource ?? before,
          summary: isResult ? out.result.summary : out.invalid.message,
          analysis: an,
          selfTest,
          attempts: isResult ? out.result.attempts : 3,
          moduleName,
          tables: tablesNeeded,
          invalid: !isResult,
        },
        prompts: [...now.prompts, { role: 'user', content: prompt }, { role: 'assistant', content: isResult ? out.result.summary : out.invalid.message }],
        recentTests: [...recent, ...now.recentTests].slice(0, 10),
      })
      // A new module takes the generated name (the editor keeps its key, so it is not remounted).
      if (now.module === '~new' && moduleName && moduleName !== '~new') {
        const conflict = an?.conflicts.some((c) => c.kind === 'module_name')
        const target = conflict ? `${moduleName}_2` : moduleName
        renameDoc(pid, '~new', target)
        clearOutline(pid, '~new')
        navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: target }, search: {}, replace: true })
      }
    },
    [pid, doc.module, gen, patch, renameDoc, clearOutline, navigate, slug],
  )

  // auto-generate from ?prompt=…&generate=1 (setup step 2, ⌘K) once, then drop `prompt`
  const autoRan = useRef(false)
  useEffect(() => {
    if (autoRan.current || !search.prompt || !search.generate) return
    autoRan.current = true
    const prompt = search.prompt
    navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, prompt: undefined, generate: undefined }), replace: true } as never)
    runGenerate(prompt)
  }, [search.prompt, search.generate, navigate, runGenerate])

  const discard = () => {
    if (!doc.draft) return
    const view = editorRef.current?.view
    const prev = doc.draft.previousSource
    if (view) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: prev }, userEvent: 'ai.discard' })
    patch(pid, doc.module, { source: prev, draft: null })
  }

  const applyTemplate = (k: TemplateKey) => {
    if (doc.module === '~new') {
      navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, dialog: 'new-module' }) } as never)
      return
    }
    const src = templateSource(k, doc.module)
    const view = editorRef.current?.view
    if (view) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: src }, userEvent: 'template' })
    gen.setState({ kind: 'idle' })
  }

  // ── editor extensions (identity changes trigger an in-place reconfigure) ──
  const completionData = useMemo(() => {
    const byKey: Record<string, string[]> = {}
    for (const s of secrets.data ?? []) byKey[s.key] = [...new Set([...(byKey[s.key] ?? []), s.environment])]
    return { functions: fns.data ?? [], reference: ref.data ?? null, tables: [...existingTables, ...virtualTables], secrets: byKey, envs }
  }, [fns.data, ref.data, existingTables, virtualTables.join(','), secrets.data, envs])
  const errorLines = useMemo(() => problems.map((p) => p.line ?? 0).filter(Boolean), [problems])
  const extensions: Extension[] = useMemo(
    () => [
      lintGutter(),
      Prec.highest(
        keymap.of([
          { key: 'Mod-s', preventDefault: true, run: () => (saveRef.current(), true) },
          { key: 'Mod-/', run: toggleComment },
          { key: 'Mod-i', run: () => (composerRef.current?.focus(), true) },
        ]),
      ),
      EditorView.updateListener.of((u) => {
        if (u.selectionSet || u.docChanged) {
          const pos = u.state.selection.main.head
          const line = u.state.doc.lineAt(pos)
          setCursor({ line: line.number, col: pos - line.from + 1 })
        }
      }),
      // layout only; the code sits in the pane's well, so the editor paints no background of its own
      EditorView.theme({ '.cm-tooltip-autocomplete ul li': { display: 'flex', alignItems: 'center' }, '&, .cm-gutters': { backgroundColor: 'transparent' } }),
      studioAutocomplete(completionData),
      lineTints({ added, errors: errorLines }),
      secretWidgets(visibleIssues, {
        onFix: (issue, anchor) => setFix({ issue, anchor }),
        onDismiss: (key) => setDismissed((s) => new Set([...s, key])),
      }),
    ],
    [completionData, added, errorLines, visibleIssues],
  )

  // global shortcuts outside the editor
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"]')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        saveRef.current()
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'i') {
        e.preventDefault()
        composerRef.current?.focus()
      } else if ((e.metaKey || e.ctrlKey) && e.key === '.') {
        e.preventDefault()
        setInspectorOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ── module ops ──
  const doDelete = async () => {
    if (!doc.moduleId) return
    setBusy(true)
    try {
      const r = (await modulesApi.remove(pid, doc.moduleId)) as { routes?: { removed?: unknown[] } }
      const n = r.routes?.removed?.length ?? 0
      removeDoc(pid, doc.module)
      clearOutline(pid, doc.module)
      // The delete succeeded. The module's own queries (detail, delete dry-run) must not be refetched — they
      // would 404 — so close the dialog, stop and drop them, then leave the page and refresh the lists.
      setDialog(null)
      qc.setQueryData<Array<{ id: string }>>(qk.modules(pid), (old) => old?.filter((m) => m.id !== doc.moduleId)) // → the parent's detail query is disabled
      qc.removeQueries({ queryKey: qk.module(pid, doc.moduleId) })
      toast.success(`Deleted ${doc.module}.py · ${n} route${n === 1 ? '' : 's'} removed`)
      navigate({ to: '/p/$project', params: { project: slug } })
      invalidate.modulesChanged(qc, pid).catch((e) => console.warn('refreshing after delete failed', e))
    } catch (e) {
      toast.error('Could not delete the module', { description: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const doRename = async (name: string) => {
    if (!doc.moduleId) return
    setBusy(true)
    setRenameError(null)
    try {
      const r = await modulesApi.update(pid, doc.moduleId, { source: doc.savedSource, name, base_sha: doc.baseSha ?? undefined })
      renameDoc(pid, doc.module, name)
      patch(pid, name, { baseSha: r.module.source_sha })
      clearOutline(pid, doc.module)
      await invalidate.modulesChanged(qc, pid)
      setDialog(null)
      navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: name }, replace: true })
    } catch (e) {
      setRenameError(isApiError(e, 'name_taken') ? `A module called "${name}" already exists.` : errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  const reloadTheirs = async () => {
    if (!doc.moduleId) return
    const m = await modulesApi.get(pid, doc.moduleId)
    const view = editorRef.current?.view
    if (view) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: m.source ?? '' }, userEvent: 'reload' })
    patch(pid, doc.module, { source: m.source ?? '', savedSource: m.source ?? '', baseSha: m.source_sha, draft: null, staleRestore: false })
    clearPersisted(pid, doc.module)
    qc.setQueryData(qk.module(pid, doc.moduleId), m)
    saver.clearConflict()
  }
  const showTheirs = async () => {
    if (!doc.moduleId) return
    const m = await modulesApi.get(pid, doc.moduleId)
    setTheirs(m.source ?? '')
    setDiffOpen('theirs')
  }

  // ── header bits ──
  const errCount = problems.length
  const stateText =
    errCount > 0
      ? `${errCount} error${errCount === 1 ? '' : 's'}, not saved`
      : dirty
        ? `Unsaved${newRoutes.length ? ` · ${newRoutes.length} new route${newRoutes.length === 1 ? '' : 's'} on save` : ''}`
        : savedUpdatedAt
          ? `Saved ${timeAgo(savedUpdatedAt)}`
          : ''
  const sandboxAvailable = sandbox.data ? sandbox.data.available : true
  const isEmptyNew = doc.module === '~new' && !doc.source && (modules.data?.length ?? 0) === 0 && !streaming && !doc.draft
  const warnCount = (doc.draft?.analysis?.warnings.length ?? 0) + secretIssues.length
  const calloutIssue = visibleIssues[0] ?? null

  return (
    <div className="flex h-full gap-2" data-testid="backend-view">
      <Tile editor as="section" className="flex min-w-0 flex-[1.15] flex-col overflow-hidden" data-testid="editor-panel">
        {/* editor head */}
        <div className="flex h-12 shrink-0 items-center gap-2 px-3" data-testid="file-bar">
          <span
            className="inline-flex h-7 shrink-0 items-center gap-2 rounded-ctl bg-foreground/5 pr-3 pl-2 font-mono text-[12.5px]"
            data-testid={`file-tab-${doc.module}`}
          >
            <FileCode2 className="size-3.5 text-muted-foreground" />
            {doc.module === '~new' ? '~new' : `${doc.module}.py`}
            {dirty && <span className="-ml-0.5 size-1.5 rounded-full bg-foreground/75" data-testid="dirty-dot" />}
          </span>
          <Tip label="Visual map · Visual + code · Code. Code is authoritative">
            <span className="inline-flex">
              <Segmented
                size="xs"
                value={editorMode}
                onChange={setEditorMode}
                aria-label="Backend editor view"
                options={[
                  { key: 'visual', label: width < 1600 ? 'Map' : 'Visual map', testId: 'backend-view-visual' },
                  { key: 'split', label: width < 1600 ? 'Split' : 'Visual + code', testId: 'backend-view-split' },
                  { key: 'code', label: 'Code', testId: 'backend-view-code' },
                ]}
              />
            </span>
          </Tip>
          <div className="min-w-0 flex-1" />
          {!narrow ? (
            <span className={cn('truncate text-xs transition-colors duration-(--d-fast)', errCount ? 'text-destructive' : 'text-muted-foreground')} data-testid="editor-state">
              {stateText}
            </span>
          ) : (
            errCount > 0 && (
              <span className="sr-only" data-testid="editor-state">
                {stateText}
              </span>
            )
          )}
          <Tip label="Open the studio SDK reference">
            <Button variant="ghost" size="sm" onClick={() => { setInspectorOpen(true); setEditorMode('code'); setInspectorTab('reference') }} data-testid="open-sdk">
              <BookOpen /> {!veryNarrow && 'SDK'}
            </Button>
          </Tip>
          {doc.moduleId && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Module actions" data-testid="module-menu" />}>
                <Ellipsis />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => navigate({ to: '/p/$project', params: { project: slug }, search: { dialog: 'create-ui', module: doc.module } })}
                  data-testid="module-create-ui"
                >
                  <Wand2 /> Create UI from these routes
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setDialog('rename')} data-testid="module-rename">
                  Rename…
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setDialog('delete')} data-testid="module-delete">
                  Delete…
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          <Tip label={`Save ${doc.module}.py`} kbd="⌘S">
            <Button size="sm" onClick={() => void doSave()} disabled={saver.saving || streaming || (!dirty && !doc.isNew)} data-testid="save-module">
              {saver.saving ? 'Saving…' : 'Save'} <Kbd>⌘S</Kbd>
            </Button>
          </Tip>
        </div>

        {doc.staleRestore && (
          <div className="px-3 pb-2" data-testid="draft-restored-stale">
            <Callout tone="warn" icon={<TriangleAlert />}>
              <div className="flex items-center gap-2">
                <span className="flex-1">{doc.module}.py changed since this draft was made</span>
                <Button variant="ghost" size="xs" onClick={showTheirs}>
                  Diff
                </Button>
                <Button variant="ghost" size="xs" onClick={reloadTheirs} data-testid="stale-discard">
                  Discard draft
                </Button>
                <Button variant="ghost" size="xs" onClick={() => patch(pid, doc.module, { staleRestore: false })} data-testid="stale-keep">
                  Keep draft
                </Button>
              </div>
            </Callout>
          </div>
        )}

        <PromptComposer
          ref={composerRef}
          state={gen.state}
          draft={doc.draft}
          source={doc.source}
          hasSource={!!doc.source.trim()}
          promptCount={doc.prompts.filter((p) => p.role === 'user').length}
          lastPrompt={[...doc.prompts].reverse().find((p) => p.role === 'user')?.content ?? null}
          initialPrompt={undefined}
          narrow={narrow}
          veryNarrow={veryNarrow}
          diffStat={diffStat}
          missingSecrets={{ count: missingSecretKeys.length, envs: missingEnvs }}
          nameConflict={!!doc.draft?.analysis?.conflicts.some((c) => c.kind === 'module_name') || saver.nameTaken}
          onGenerate={runGenerate}
          onStop={gen.stop}
          onDiscard={discard}
          onDiff={() => setDiffOpen('draft')}
          onToggleTable={(name, create) =>
            doc.draft && patch(pid, doc.module, { draft: { ...doc.draft, tables: doc.draft.tables.map((t) => (t.name === name ? { ...t, create } : t)) } })
          }
          onEditColumns={(name) => navigate({ to: '/p/$project/data/$table', params: { project: slug, table: name } })}
          onRenameDraftModule={(name) => {
            if (doc.isNew) {
              renameDoc(pid, doc.module, name)
              navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: name }, replace: true })
            }
          }}
          onTemplate={applyTemplate}
          onRetry={() => gen.setState({ kind: 'idle' })}
        />

        {isEmptyNew ? (
          <div className="flex flex-1 items-center justify-center" data-testid="backend-empty">
            <EmptyState
              icon={<Code2 />}
              title="Write your backend by hand"
              dust
              actions={
                <>
                  <span className="text-xs text-muted-foreground">Templates:</span>
                  {(Object.keys(TEMPLATE_LABELS) as TemplateKey[]).map((k) => (
                    <Button
                      key={k}
                      variant="outline"
                      size="sm"
                      className="rounded-full"
                      onClick={() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, dialog: 'new-module' }) } as never)}
                    >
                      {TEMPLATE_LABELS[k]}
                    </Button>
                  ))}
                </>
              }
            >
              Or describe your API above — for example “{STARTERS[0].prompt}”
            </EmptyState>
          </div>
        ) : (
          <div className={cn('relative flex min-h-0 flex-1', editorMode === 'split' && 'flex-col xl:flex-row')}>
            {editorMode !== 'code' && (
              <div className="min-h-0 min-w-0 flex-1" data-testid="backend-map-pane">
                <CodeMapPanel
                  pid={pid}
                  moduleId={doc.moduleId}
                  moduleName={doc.module}
                  source={doc.source}
                  enabled={!streaming}
                  onJump={(line) => {
                    setEditorMode('split')
                    requestAnimationFrame(() => {
                      goToLine(line)
                    })
                  }}
                />
              </div>
            )}
            <div className={cn('relative min-h-0 min-w-0 flex-1 bg-(--well)', editorMode === 'visual' && 'hidden', editorMode === 'split' && 'shadow-[inset_0_1px_0_var(--hairline)] xl:shadow-[inset_1px_0_0_var(--hairline)]')}>
              {streaming && (
                <span className="absolute top-2 right-3 z-10 rounded-chip bg-recess px-2 py-0.5 text-[11.5px] text-muted-foreground" data-testid="readonly-tag">
                  Read-only while generating
                </span>
              )}
              <CodeEditor
                key={doc.editorKey}
                ref={editorRef}
                bare
                testId="code-editor"
                language="python"
                height="100%"
                value={doc.source}
                onChange={(v) => patch(pid, doc.module, { source: v })}
                readOnly={streaming}
                extensions={extensions}
                diagnostics={diagnostics}
                placeholder={doc.module === '~new' ? 'Describe your API above, or start writing Python…' : undefined}
              />
            </div>
          </div>
        )}

        {/* missing secret: one flat line with the fix */}
        {calloutIssue && (
          <div className="shrink-0 px-3 pt-2" data-testid="secret-callout" data-kind={calloutIssue.kind}>
            <Callout tone="warn" icon={<KeyRound />} className="min-h-[30px] w-fit max-w-full bg-[color-mix(in_srgb,var(--warning)_11%,transparent)] py-0 pr-1 pl-2.5 text-[12px]">
              <div className="flex items-center gap-2">
                <span className="min-w-0 truncate text-foreground">
                  {calloutIssue.kind === 'not_bound' ? (
                    <>
                      <b className="font-semibold">{calloutIssue.key}</b> isn't bound to a host
                    </>
                  ) : calloutIssue.presentIn.length ? (
                    <>
                      <b className="font-semibold">{calloutIssue.key}</b> is set in <b className="font-semibold">{calloutIssue.presentIn.join(', ')}</b>, missing in{' '}
                      <b className="font-semibold">{calloutIssue.missingIn.join(', ')}</b>
                    </>
                  ) : (
                    <>
                      <b className="font-semibold">{calloutIssue.key}</b> is missing in <b className="font-semibold">{calloutIssue.missingIn.join(', ')}</b>
                    </>
                  )}
                  {visibleIssues.length > 1 && <span className="text-muted-foreground"> · +{visibleIssues.length - 1} more</span>}
                </span>
                <Button
                  variant="outline"
                  size="xs"
                  onClick={(e) => setFix({ issue: calloutIssue, anchor: e.currentTarget })}
                  data-testid="secret-callout-fix"
                >
                  {calloutIssue.kind === 'not_bound' ? 'Bind host' : `Add to ${calloutIssue.missingIn.join(', ')}`}
                </Button>
              </div>
            </Callout>
          </div>
        )}

        {/* status bar */}
        <div className="flex h-8 shrink-0 items-center gap-3.5 px-3 text-[11.5px] text-muted-foreground" data-testid="status-bar">
          {errCount > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-destructive" data-testid="status-errors">
              <CircleX className="size-3" /> {errCount} error{errCount === 1 ? '' : 's'}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-success-text" data-testid="status-ok">
              <CircleCheck className="size-3" /> No syntax errors
            </span>
          )}
          <span
            className={cn('inline-flex items-center gap-1.5 transition-colors duration-(--d-base)', warnCount > 0 && 'text-warning-text')}
            data-testid="status-warnings"
          >
            <TriangleAlert className="size-3" />
            <span className="inline-flex gap-[.3em]">
              <Roll value={warnCount} />
              <span>warning{warnCount === 1 ? '' : 's'}</span>
            </span>
          </span>
          {analysis.error && <span className="truncate text-destructive">analysis failed: {analysis.error}</span>}
          <div className="flex-1" />
          <span className="tabular-nums" data-testid="cursor-pos">
            Ln {cursor.line}, Col {cursor.col}
          </span>
          {!veryNarrow && <span>Python 3</span>}
          {sandbox.data &&
            (sandbox.data.available ? (
              <span className="inline-flex items-center gap-1.5" data-testid="sandbox-badge" data-available="true">
                <ShieldCheck className="size-3" /> Sandbox · {sandbox.data.backend}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-chip bg-destructive-soft px-1.5 text-destructive" data-testid="sandbox-badge" data-available="false">
                <ShieldAlert className="size-3" /> Sandbox unavailable
              </span>
            ))}
        </div>
      </Tile>

      {inspectorOpen && editorMode === 'code' && (
        <Tile editor as="section" className="flex min-w-[340px] flex-1 flex-col overflow-hidden" data-testid="inspector-panel">
          <Tabs value={inspectorTab} onValueChange={(v) => setInspectorTab(v as typeof inspectorTab)} className="h-full">
            <TabsList className="h-12 px-3 shadow-none">
              <TabsTab value="test" data-testid="inspector-tab-test">
                <Play /> Test
              </TabsTab>
              <TabsTab value="reference" data-testid="inspector-tab-reference">
                <BookOpen /> Reference
              </TabsTab>
              <TabsTab value="client" data-testid="inspector-tab-client">
                <FileType2 /> client.ts
              </TabsTab>
            </TabsList>
            <TabsPanel value="test" className="min-h-0 overflow-hidden">
              <TestPanel
                routes={testRoutes}
                selected={selectedRoute}
                onSelect={setSelectedRoute}
                useDraftRun={dirty}
                source={doc.source}
                moduleName={doc.module}
                moduleId={doc.moduleId}
                virtualTables={virtualTables}
                draftTables={draftColumns}
                dirty={dirty}
                sandboxAvailable={sandboxAvailable}
                narrow={narrow}
                onGoToLine={goToLine}
                recentTests={doc.recentTests}
                onRecord={(t) => {
                  const cur = useDocStore.getState().docs[docKey(pid, doc.module)]
                  patch(pid, doc.module, { recentTests: [t, ...(cur?.recentTests ?? [])].slice(0, 10) })
                }}
              />
            </TabsPanel>
            <TabsPanel value="reference" className="min-h-0 overflow-hidden">
              <ReferencePanel
                onInsert={(text) => {
                  const view = editorRef.current?.view
                  if (!view || streaming) return
                  const pos = view.state.selection.main.head
                  view.dispatch({ changes: { from: pos, insert: text }, selection: { anchor: pos + text.length } })
                  view.focus()
                }}
              />
            </TabsPanel>
            <TabsPanel value="client" className="min-h-0 overflow-hidden">
              <ClientTsPanel />
            </TabsPanel>
          </Tabs>
        </Tile>
      )}

      {/* secret widget fix popover, anchored to the widget */}
      <Popover open={!!fix} onOpenChange={(o) => !o && setFix(null)}>
        <PopoverContent anchor={fix?.anchor ?? null} className="w-[340px]" data-testid="secret-fix-popover">
          {fix && fix.issue.kind !== 'not_bound' && (
            <CreateSecretForm
              secretKey={fix.issue.key}
              keyReadOnly
              environments={fix.issue.missingIn}
              hosts={[...doc.source.matchAll(/https?:\/\/([A-Za-z0-9.-]+)/g)].map((m) => m[1]).filter((h, i, a) => a.indexOf(h) === i)}
              onCreated={() => setFix(null)}
              onCancel={() => setFix(null)}
              testId="widget-secret"
            />
          )}
          {fix && fix.issue.kind === 'not_bound' && <BindHostForm secretKey={fix.issue.key} host={fix.issue.host ?? ''} onDone={() => setFix(null)} />}
        </PopoverContent>
      </Popover>

      <BreakingChangesDialog
        open={!!saver.breaking}
        module={doc.module}
        changes={saver.breaking ?? []}
        onCancel={saver.clearBreaking}
        onConfirm={() => {
          saver.clearBreaking()
          void doSave({ confirmedBreaking: true })
        }}
      />
      <ModuleChangedDialog
        open={!!saver.conflict}
        module={doc.module}
        updatedAt={saver.conflict?.updatedAt ?? null}
        onClose={saver.clearConflict}
        onShowDiff={showTheirs}
        onReload={reloadTheirs}
        onOverwrite={() => {
          saver.clearConflict()
          void doSave({ overwrite: true })
        }}
      />
      {doc.moduleId && (
        <DeleteModuleDialog
          open={dialog === 'delete'}
          module={doc.module}
          moduleId={doc.moduleId}
          tables={[...new Set([...doc.savedSource.matchAll(/studio\.db\.table\(\s*["']([\w-]+)["']/g)].map((m) => m[1]))].filter((t) => existingTables.includes(t))}
          onClose={() => setDialog(null)}
          onConfirm={doDelete}
          busy={busy}
        />
      )}
      <RenameModuleDialog
        open={dialog === 'rename'}
        module={doc.module}
        taken={(modules.data ?? []).map((m) => m.name)}
        onClose={() => {
          setDialog(null)
          setRenameError(null)
        }}
        onConfirm={doRename}
        busy={busy}
        serverError={renameError}
      />
      <DiffDialog
        open={diffOpen !== null}
        onClose={() => setDiffOpen(null)}
        title={diffOpen === 'theirs' ? `${doc.module}.py: saved vs yours` : `${doc.module}.py: AI draft`}
        leftLabel={diffOpen === 'theirs' ? 'saved (theirs)' : 'before the draft'}
        rightLabel={diffOpen === 'theirs' ? 'your editor' : 'draft'}
        left={diffOpen === 'theirs' ? (theirs ?? '') : (doc.draft?.previousSource ?? '')}
        right={doc.source}
      />
    </div>
  )
}

function BindHostForm({ secretKey, host, onDone }: { secretKey: string; host: string; onDone: () => void }) {
  const { pid } = useProjectCtx()
  const secrets = useSecrets(pid)
  const update = useUpdateSecret(pid)
  const [hosts, setHosts] = useState<string[]>(host ? [host] : [])
  const [error, setError] = useState<string | null>(null)
  const rows = (secrets.data ?? []).filter((s) => s.key === secretKey && s.scope === 'project')
  return (
    <form
      className="flex flex-col gap-3"
      data-testid="bind-host-form"
      onSubmit={async (e) => {
        e.preventDefault()
        setError(null)
        try {
          for (const r of rows) await update.mutateAsync({ id: r.id, body: { allowed_hosts: [...new Set([...(r.allowed_hosts ?? []), ...hosts])] } })
          onDone()
        } catch (err) {
          setError(errorMessage(err))
        }
      }}
    >
      <div className="text-[13px] font-medium">
        Bind <code>{secretKey}</code> to a host
      </div>
      <ChipsInput value={hosts} onChange={setHosts} testId="bind-host-chips" />
      {error && <div className="text-xs text-destructive">{error}</div>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!hosts.length || update.isPending} data-testid="bind-host-save">
          Bind
        </Button>
      </div>
    </form>
  )
}
