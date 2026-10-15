/**
 * ProjectTopBar (UI-DESIGN §2.4):
 * `[luna] / [● Project ⇕] [Project | Workflows n] … [Search ⌘K] | [● dev | ● prod] [Release] [☀]`
 */
import { useState } from 'react'
import { Link, useMatchRoute, useNavigate } from '@tanstack/react-router'
import { ChevronsUpDown, LayoutGrid, Moon, Plus, Rocket, Search, Sun, Workflow, Check } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useMissingSecrets, useProjectSummary, useProjects } from '../hooks/useProjectData'
import { StatusDot, type DotTone } from '@/shared/components/ui/status-dot'
import { Kbd } from '@/shared/components/ui/kbd'
import { Button } from '@/shared/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { useTheme } from '@/shared/components/theme-provider'
import { cn } from '@/shared/lib/utils'
import { CommandPalette } from './CommandPalette'
import { NewProjectDialog } from './NewProjectDialog'
import type { HealthStatus } from '@/shared/api/types'

export function healthTone(s: HealthStatus | undefined): DotTone {
  if (s === 'ok' || s === 'healthy') return 'ok'
  if (s === 'degraded') return 'warn'
  if (s === 'failing') return 'err'
  return 'idle'
}

export function useToggleTheme() {
  const { theme, setTheme } = useTheme()
  const isDark =
    theme === 'dark' || (theme === 'system' && typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  return { isDark, toggle: () => setTheme(isDark ? 'light' : 'dark') }
}

export function ThemeToggle() {
  const { isDark, toggle } = useToggleTheme()
  return (
    <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label="Toggle theme" data-testid="theme-toggle">
      {isDark ? <Moon /> : <Sun />}
    </Button>
  )
}

export function Brand() {
  return (
    <Link
      to="/projects"
      className="rounded-md bg-primary px-2 text-[13px] font-semibold leading-5 text-primary-foreground"
      data-testid="brand"
    >
      luna
    </Link>
  )
}

export function ProjectSwitcher() {
  const { project } = useProjectCtx()
  const projects = useProjects()
  const summary = useProjectSummary(project.id)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          data-testid="project-switcher"
          className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 font-medium hover:bg-accent"
        >
          <StatusDot tone={healthTone(summary.data?.health.status)} data-testid="project-health-dot" />
          <span className="max-w-48 truncate">{project.name}</span>
          <ChevronsUpDown className="size-3 text-muted-foreground" />
        </PopoverTrigger>
        <PopoverContent className="w-64 p-1">
          <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Projects
          </div>
          <div className="max-h-72 overflow-y-auto">
            {(projects.data ?? []).map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  setOpen(false)
                  navigate({ to: '/p/$project', params: { project: p.slug } })
                }}
                className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left hover:bg-accent"
              >
                <StatusDot tone={healthTone(p.health?.status)} />
                <span className="flex-1 truncate">{p.name}</span>
                {p.id === project.id && <Check className="size-3.5 text-muted-foreground" />}
              </button>
            ))}
          </div>
          <div className="my-1 h-px bg-border" />
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              setNewOpen(true)
            }}
            className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <Plus className="size-3.5" /> New project
          </button>
        </PopoverContent>
      </Popover>
      <NewProjectDialog open={newOpen} onOpenChange={setNewOpen} />
    </>
  )
}

export function EnvSwitch() {
  const { project, pid, viewEnv, setViewEnv } = useProjectCtx()
  const missing = useMissingSecrets(pid)
  const missingIn = (env: string) => (missing.data ?? []).some((m) => m.missing_in.includes(env))
  return (
    <Tooltip>
      <TooltipTrigger
        render={<div className="flex items-center gap-0.5 rounded-lg bg-muted/60 p-0.5" data-testid="env-switch" />}
      >
        {project.environments.map((e) => {
          const on = e.name === viewEnv
          return (
            <button
              key={e.name}
              type="button"
              data-testid={`env-${e.name}`}
              aria-pressed={on}
              onClick={() => setViewEnv(e.name)}
              className={cn(
                'inline-flex h-[26px] items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground',
                on && 'bg-card text-foreground shadow-xs',
              )}
            >
              <StatusDot tone={missingIn(e.name) ? 'warn' : 'ok'} data-testid={`env-dot-${e.name}`} />
              {e.name}
            </button>
          )
        })}
      </TooltipTrigger>
      <TooltipContent>Viewing {viewEnv}. Tables are shared by all environments.</TooltipContent>
    </Tooltip>
  )
}

export function ProjectTopBar() {
  const { project, slug } = useProjectCtx()
  const summary = useProjectSummary(project.id)
  const matchRoute = useMatchRoute()
  const onWorkflows = !!matchRoute({ to: '/p/$project/workflows', fuzzy: true })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const navigate = useNavigate()
  const live = project.live_environment
  const liveRelease = summary.data?.active_releases?.[live]
  const hasReleaseEnv = project.environments.some((e) => e.kind === 'release')

  return (
    <header
      data-testid="topbar"
      className="absolute inset-x-3 top-3 z-20 flex h-11 items-center gap-1 rounded-xl border border-panel-border bg-card/82 pl-2.5 pr-2 shadow-lg backdrop-blur-xl"
    >
      <Brand />
      <span className="mx-0.5 text-base text-border">/</span>
      <ProjectSwitcher />
      <nav className="ml-2 flex gap-0.5 rounded-lg bg-muted/60 p-0.5" aria-label="Project pages">
        <Link
          to="/p/$project"
          params={{ project: slug }}
          data-testid="tab-project"
          className={cn(
            'inline-flex h-[26px] items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground',
            !onWorkflows && 'bg-card text-foreground shadow-xs',
          )}
        >
          <LayoutGrid className="size-3.5" /> Project
        </Link>
        <Link
          to="/p/$project/workflows"
          params={{ project: slug }}
          data-testid="tab-workflows"
          className={cn(
            'inline-flex h-[26px] items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground',
            onWorkflows && 'bg-card text-foreground shadow-xs',
          )}
        >
          <Workflow className="size-3.5" /> Workflows
          {summary.data && <span className="opacity-55">{summary.data.counts.workflows}</span>}
        </Link>
      </nav>
      <div className="flex-1" />
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        data-testid="open-palette"
        className="hidden h-7 items-center gap-2 rounded-md px-2 text-[12.5px] text-muted-foreground hover:bg-accent md:inline-flex"
      >
        <Search className="size-3.5" /> <span className="hidden lg:inline">Search or jump to…</span> <Kbd>⌘K</Kbd>
      </button>
      <span className="mx-1.5 h-4 w-px bg-border" />
      <EnvSwitch />
      {hasReleaseEnv && (
        <Button
          variant="outline"
          size="sm"
          className="ml-1.5"
          data-testid="open-release"
          onClick={() => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, sheet: 'release' }) } as never)}
        >
          <Rocket /> Release
          {liveRelease && <span className="text-muted-foreground">({live} #{liveRelease.number})</span>}
        </Button>
      )}
      <ThemeToggle />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </header>
  )
}
