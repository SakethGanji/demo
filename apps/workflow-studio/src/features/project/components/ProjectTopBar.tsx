/**
 * ProjectTopBar (DESIGN §1, UI-DESIGN §2.4), the slim glass bar of the home screen and every system page:
 * `[⌂][⊞] [Project ▾] › Page … [Search ⌘K] [● dev | ● prod] [Release] [☀]`. Home and Jump (HomeNav) are the way
 * back and the way across; the crumb names the page you opened.
 */
import { useEffect, useState } from 'react'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { ChevronRight, ChevronsUpDown, Plus, Rocket } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useMissingSecrets, useProjectSummary, useProjects } from '../hooks/useProjectData'
import { healthTone } from '../lib/healthTone'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Button } from '@/shared/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { SearchMenuList } from '@/shared/components/ui/search-menu'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { useViewportWidth } from '@/shared/hooks/useViewport'
import { CommandPalette } from './CommandPalette'
import { NewProjectDialog } from './NewProjectDialog'
import { SearchTrigger, SegTrack, ThemeToggle, TopBarFrame, segItem } from './TopBar'
import { HomeNav } from './HomeNav'
import { PLACES } from '../lib/places'

export function ProjectAvatar({ name, size = 18, className }: { name: string; size?: number; className?: string }) {
  return (
    <span
      className={cn('inline-flex shrink-0 items-center justify-center rounded-chip brand-fill font-semibold', className)}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.55) }}
      aria-hidden
    >
      {name.trim().charAt(0).toUpperCase() || 'P'}
    </span>
  )
}

function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  return !!t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || !!t.closest('.cm-editor'))
}

/** The project name with its health; `compact` (the folded rail) shows the avatar alone. */
export function ProjectSwitcher({ compact, className }: { compact?: boolean; className?: string }) {
  const { project } = useProjectCtx()
  const projects = useProjects()
  const summary = useProjectSummary(project.id)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)

  // ⌘P opens the switcher
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'p') {
        e.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <Tip label={compact ? `${project.name} · switch project` : 'Switch project'} kbd="⌘P" side={compact ? 'right' : 'bottom'}>
          <PopoverTrigger
            data-testid="project-switcher"
            aria-label={compact ? project.name : undefined}
            className={cn(
              'relative inline-flex h-7 min-w-0 items-center gap-2 rounded-ctl text-[13px] font-medium outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus aria-expanded:bg-(--hover-fill)',
              compact ? 'w-9 shrink-0 justify-center' : 'px-2',
              className,
            )}
          >
            <ProjectAvatar name={project.name} size={compact ? 22 : 18} />
            {!compact && <span className="min-w-0 flex-1 truncate text-left">{project.name}</span>}
            <StatusDot
              tone={healthTone(summary.data?.health.status)}
              data-testid="project-health-dot"
              className={compact ? 'absolute -top-0.5 right-0' : undefined}
            />
            {!compact && <ChevronsUpDown className="size-3 shrink-0 text-muted-foreground" />}
          </PopoverTrigger>
        </Tip>
        <PopoverContent className="w-72 p-0" side={compact ? 'right' : 'bottom'}>
          <SearchMenuList
            placeholder="Find a project…"
            testId="project-switcher-menu"
            groups={[
              {
                label: 'Projects',
                items: (projects.data ?? []).map((p) => ({
                  key: p.slug,
                  keywords: p.name,
                  icon: <StatusDot tone={healthTone(p.health?.status)} flat />,
                  label: p.name,
                  sub: p.counts ? `${p.counts.routes} route${p.counts.routes === 1 ? '' : 's'}` : undefined,
                  checked: p.id === project.id,
                  onSelect: () => {
                    setOpen(false)
                    navigate({ to: '/p/$project', params: { project: p.slug } })
                  },
                })),
              },
              {
                items: [
                  {
                    key: '__new',
                    keywords: 'new project create',
                    icon: <Plus />,
                    label: 'New project',
                    onSelect: () => {
                      setOpen(false)
                      setNewOpen(true)
                    },
                  },
                ],
              },
            ]}
          />
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
    <Tip label={`Viewing ${viewEnv}. Tables are shared by all environments.`} side="bottom">
      <SegTrack dep={viewEnv} data-testid="env-switch">
        {project.environments.map((e) => {
          const on = e.name === viewEnv
          return (
            <button
              key={e.name}
              type="button"
              data-testid={`env-${e.name}`}
              aria-pressed={on}
              data-on={on ? '' : undefined}
              onClick={() => setViewEnv(e.name)}
              className={segItem}
            >
              <StatusDot tone={missingIn(e.name) ? 'warn' : 'ok'} flat data-testid={`env-dot-${e.name}`} />
              {e.name}
            </button>
          )
        })}
      </SegTrack>
    </Tip>
  )
}

export function ProjectTopBar() {
  const { project } = useProjectCtx()
  const summary = useProjectSummary(project.id)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const navigate = useNavigate()
  const live = project.live_environment
  const liveRelease = summary.data?.active_releases?.[live]
  const hasReleaseEnv = project.environments.some((e) => e.kind === 'release')
  const phone = useViewportWidth() < 640
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const seg = pathname.split('/')[3]
  const place = PLACES.find((p) => p.key === seg)
  const openRelease = () => navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, sheet: 'release' }) } as never)

  // ⇧R opens the release sheet (ignored while typing)
  useEffect(() => {
    if (!hasReleaseEnv) return
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey && e.key === 'R' && !isTyping(e) && !document.querySelector('[role="dialog"]')) {
        e.preventDefault()
        openRelease()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <TopBarFrame data-testid="topbar" className="pl-1">
      <HomeNav />
      <span className="mx-1 h-4 w-px shrink-0 bg-hairline" aria-hidden />
      <ProjectSwitcher className={phone ? 'max-w-[150px]' : 'max-w-[240px]'} />
      {place && (
        <span className="flex min-w-0 items-center gap-1.5 text-[13px] font-medium" data-testid="topbar-page">
          <ChevronRight className="size-3.5 shrink-0 text-faint" aria-hidden />
          <place.Icon className={cn('size-4 shrink-0 stroke-[1.75]', place.text)} aria-hidden />
          <span className="truncate">{place.label}</span>
        </span>
      )}
      <div className="flex-1" />
      {!phone && <SearchTrigger onClick={() => setPaletteOpen(true)} />}
      <span className="mx-1" />
      {/* phones keep the brand, the project and the theme; environments and releases need a wider screen */}
      {!phone && <EnvSwitch />}
      {hasReleaseEnv && !phone && (
        <Tip label="Create a release" kbd="⇧R" side="bottom">
          <Button variant="outline" size="sm" className="ml-1.5" data-testid="open-release" onClick={openRelease}>
            <Rocket /> Release
            {liveRelease && (
              <span className="text-muted-foreground">
                ({live} #{liveRelease.number})
              </span>
            )}
          </Button>
        </Tip>
      )}
      <ThemeToggle />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </TopBarFrame>
  )
}

