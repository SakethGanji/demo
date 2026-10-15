/**
 * ⌘K palette (UI-DESIGN §2.4): Routes, Modules, Tables, Apps, Workflows, Runs, Secrets, Actions.
 */
import { useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { AppWindow, Database, FileCode2, History, KeyRound, Plus, Settings, Sparkles, Workflow, Rocket } from 'lucide-react'
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/shared/components/ui/command'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { useProjectCtx } from '../ProjectContext'
import {
  useModules,
  useProjectApps,
  useProjectWorkflows,
  useRecentRuns,
  useRoutes,
  useSecrets,
  useTables,
} from '../hooks/useProjectData'
import { moduleNameOf } from '../lib/routes'
import { useToggleTheme } from './ProjectTopBar'

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const { toggle } = useToggleTheme()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        onOpenChange(true)
      }
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onOpenChange, toggle])

  const routes = useRoutes(pid)
  const modules = useModules(pid)
  const tables = useTables(pid)
  const apps = useProjectApps(pid)
  const workflows = useProjectWorkflows(pid)
  const runs = useRecentRuns(pid, { limit: 10 })
  const secrets = useSecrets(pid)

  const go = (fn: () => void) => {
    onOpenChange(false)
    fn()
  }
  const P = { project: slug }
  const secretKeys = [...new Set((secrets.data ?? []).filter((s) => s.scope === 'project').map((s) => s.key))]

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Search" className="sm:max-w-xl">
      <Command data-testid="command-palette">
        <CommandInput placeholder="Search routes, modules, tables, apps, runs…" />
        <CommandList className="max-h-[420px]">
          <CommandEmpty>No results.</CommandEmpty>
          {(routes.data?.length ?? 0) > 0 && (
            <CommandGroup heading="Routes">
              {routes.data!.map((r) => (
                <CommandItem
                  key={r.id}
                  value={`route ${r.method} ${r.path} ${r.name}`}
                  onSelect={() =>
                    go(() => {
                      const m = moduleNameOf(r, modules.data)
                      if (m) navigate({ to: '/p/$project/backend/$module', params: { ...P, module: m }, search: { route: r.name } })
                      else navigate({ to: '/p/$project', params: P })
                    })
                  }
                >
                  <MethodBadge method={r.method} size="sm" />
                  <span className="font-mono text-xs">{r.path}</span>
                  <span className="ml-auto font-mono text-[11px] text-muted-foreground">{r.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(modules.data?.length ?? 0) > 0 && (
            <CommandGroup heading="Modules">
              {modules.data!.map((m) => (
                <CommandItem
                  key={m.id}
                  value={`module ${m.name}.py`}
                  onSelect={() => go(() => navigate({ to: '/p/$project/backend/$module', params: { ...P, module: m.name } }))}
                >
                  <FileCode2 /> <span className="font-mono text-xs">{m.name}.py</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(tables.data?.length ?? 0) > 0 && (
            <CommandGroup heading="Tables">
              {tables.data!.map((t) => (
                <CommandItem
                  key={t.id}
                  value={`table ${t.name}`}
                  onSelect={() => go(() => navigate({ to: '/p/$project/data/$table', params: { ...P, table: t.name } }))}
                >
                  <Database /> {t.name}
                  <span className="ml-auto text-[11px] text-muted-foreground">{t.row_count ?? 0} rows</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(apps.data?.length ?? 0) > 0 && (
            <CommandGroup heading="Apps">
              {apps.data!.map((a) => (
                <CommandItem
                  key={a.id}
                  value={`app ${a.name}`}
                  onSelect={() => go(() => navigate({ to: '/p/$project/apps/$appId', params: { ...P, appId: a.id } }))}
                >
                  <AppWindow /> {a.name}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(workflows.data?.length ?? 0) > 0 && (
            <CommandGroup heading="Workflows">
              {workflows.data!.map((w) => (
                <CommandItem
                  key={w.id}
                  value={`workflow ${w.name}`}
                  onSelect={() =>
                    go(() => navigate({ to: '/p/$project/workflows/$workflowId', params: { ...P, workflowId: w.id } }))
                  }
                >
                  <Workflow /> {w.name}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(runs.data?.runs.length ?? 0) > 0 && (
            <CommandGroup heading="Runs">
              {runs.data!.runs.map((r) => (
                <CommandItem
                  key={r.id}
                  value={`run ${r.route ?? r.workflow_name} ${r.id}`}
                  onSelect={() => go(() => navigate({ to: '/p/$project/runs/$executionId', params: { ...P, executionId: r.id } }))}
                >
                  <History /> {r.route ?? r.workflow_name}
                  <span className="ml-auto text-[11px] text-muted-foreground">{r.status}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {secretKeys.length > 0 && (
            <CommandGroup heading="Secrets">
              {secretKeys.map((k) => (
                <CommandItem key={k} value={`secret ${k}`} onSelect={() => go(() => navigate({ to: '/p/$project/secrets', params: P }))}>
                  <KeyRound /> <span className="font-mono text-xs">{k}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandGroup heading="Actions">
            <CommandItem
              value="action generate backend with AI"
              onSelect={() => go(() => navigate({ to: '/p/$project/backend/$module', params: { ...P, module: '~new' }, search: { generate: 1 } }))}
            >
              <Sparkles className="text-ai" /> Generate backend with AI
            </CommandItem>
            <CommandItem
              value="action create ui from backend"
              onSelect={() => go(() => navigate({ to: '/p/$project', params: P, search: { dialog: 'create-ui' } }))}
            >
              <AppWindow /> Create UI from backend
            </CommandItem>
            <CommandItem
              value="action new module"
              onSelect={() => go(() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, dialog: 'new-module' }) } as never))}
            >
              <Plus /> New module
            </CommandItem>
            <CommandItem
              value="action new table"
              onSelect={() => go(() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, dialog: 'new-table' }) } as never))}
            >
              <Database /> New table
            </CommandItem>
            <CommandItem
              value="action release"
              onSelect={() => go(() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, sheet: 'release' }) } as never))}
            >
              <Rocket /> Release
            </CommandItem>
            <CommandItem
              value="action settings"
              onSelect={() => go(() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, sheet: 'settings' }) } as never))}
            >
              <Settings /> Project settings
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
