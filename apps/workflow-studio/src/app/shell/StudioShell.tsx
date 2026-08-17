/**
 * The studio app shell — the long-deferred B4 Q1, answered.
 *
 * WHY THIS IS A PATHLESS LAYOUT ROUTE AND NOT `__root`
 *
 * Every prototype assumes a persistent chrome, but `/editor`, `/builder`, `/`
 * and `/projects` are theming-only: they may pick up new token values and
 * nothing else. Putting this header in `__root` would wrap all four in a new
 * 46px band and reflow them, which is a layout change to code that is out of
 * scope.
 *
 * A pathless layout route is the mechanism for "these routes share a chrome"
 * without touching the others. It contributes no URL segment, so `__root`'s
 * `useMatchRoute` calls still match `/data` and `/catalog` exactly as before
 * and that file needed no edit at all. The only structural change is which
 * parent those two routes name.
 *
 * When the other pages come into scope, adopting the shell is re-parenting one
 * line each — which is why the nav already links to them.
 *
 * WHAT IS DELIBERATELY NOT HERE
 *
 * The prototype's topbar also carries an environment chip ("PROD · US-EAST")
 * and a live-sync lamp. Nothing in the studio knows either fact today, and a
 * hardcoded "PROD" is the kind of decoration that gets believed. They land
 * when something real backs them.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useMatchRoute } from '@tanstack/react-router'
import { Bell, Moon, Sun } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { ANALYTICS_BASE } from '@/shared/lib/analyticsClient'
import { useTheme } from '@/shared/components/theme-provider'
import { SeatSwitcher } from '@/features/datasets/components/SeatSwitcher'
import { StudioCommandPalette } from './StudioCommandPalette'

/**
 * Top-level destinations only.
 *
 * The dataset-scoped tools — query, aggregate, column, pivot, sampling,
 * ingest — are deliberately NOT here. They act on a dataset you have already
 * chosen, so they belong to the cockpit and the command palette, not to a
 * global bar. Putting nine items in a header would spend the one thing this
 * bar is for: telling you where you are.
 */
const NAV = [
  { to: '/projects', label: 'Workflows' },
  { to: '/data', label: 'Datasets' },
  { to: '/catalog', label: 'Catalog' },
  { to: '/runs', label: 'Runs' },
  { to: '/admin', label: 'Admin' },
] as const

function NavLink({ to, label }: { to: string; label: string }) {
  const matchRoute = useMatchRoute()
  const active = Boolean(matchRoute({ to, fuzzy: true }))

  return (
    <Link
      to={to}
      className={cn(
        'relative flex h-[30px] items-center rounded-md px-2.5 text-body transition-colors',
        active ? 'font-medium text-foreground' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {label}
      {/* The active route is the shell's ONE accent use.
       *
       * Rule 1: the accent means scope and liveness — "what am I looking at,
       * and is it current?" — at roughly four uses per screen. This is the
       * canonical one, so nothing else in this header may take it. Repeated
       * active state (tabs, toggles, the pager's current step) is bought with
       * value and elevation instead; see `ui/tabs.tsx`. */}
      {active && (
        <span
          aria-hidden="true"
          className="absolute right-2.5 bottom-[-9px] left-2.5 h-[2px] rounded-t-[2px] bg-[var(--sig)] shadow-[0_0_12px_-1px_var(--sig)]"
        />
      )}
    </Link>
  )
}

/** A 30px chrome button. Bare glyph, no tile — the prototype's `.iconbtn`. */
function IconButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid size-[30px] place-items-center rounded-[7px] text-muted-foreground transition-colors hover:bg-white/5 hover:text-foreground"
    >
      {children}
    </button>
  )
}

export function StudioShell() {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const { theme, setTheme } = useTheme()

  /**
   * The theme actually on screen, which is NOT the stored preference.
   *
   * Two traps here, both hit in order:
   *
   *  1. The stored preference defaults to `system`, so `theme === 'dark'` is
   *     false on a dark screen. Toggling on that comparison set `dark` when it
   *     was already dark — the button did nothing until pressed twice.
   *  2. Reading `document.documentElement.classList` during render is stale by
   *     one frame: the provider applies that class in an effect, so the render
   *     that computes the toggle still sees the OLD class. That made the
   *     SECOND press a no-op instead of the first.
   *
   * Deriving it from state fixes both, because `theme` changes synchronously
   * with the click.
   */
  const systemPrefersDark = useMemo(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches,
    [],
  )
  const resolved = theme === 'system' ? (systemPrefersDark ? 'dark' : 'light') : theme
  const toggleTheme = () => setTheme(resolved === 'dark' ? 'light' : 'dark')

  // The host the studio is actually talking to, not a hardcoded environment
  // name. `ANALYTICS_BASE` is chosen by hostname at startup.
  const envLabel = useMemo(() => {
    try {
      const u = new URL(ANALYTICS_BASE, window.location.origin)
      return `${u.hostname}:${u.port || (u.protocol === 'https:' ? '443' : '80')}`
    } catch {
      return 'api'
    }
  }, [])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setPaletteOpen((open) => !open)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="flex h-[46px] shrink-0 items-center gap-[18px] bg-[image:var(--chrome-bar)] px-4 shadow-[var(--chrome-shadow)]">
        <Link to="/" className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="size-[21px] rounded-[5px] bg-secondary shadow-[var(--hi)]"
          />
          <span className="text-label font-medium whitespace-nowrap text-foreground">
            Command&nbsp;Studio
          </span>
        </Link>

        {/* Environment scope. Real, not decorative: the studio picks its
          * backend by hostname, so this names the API it is actually talking
          * to rather than a hardcoded "PROD" that would be a lie the moment
          * anyone ran it anywhere else. */}
        <span className="flex shrink-0 items-center gap-1.5 font-mono text-micro text-muted-foreground">
          <span
            aria-hidden="true"
            className="size-[5px] shrink-0 rounded-full bg-[var(--st-warn)]"
          />
          {envLabel}
        </span>

        <nav className="flex items-center gap-1">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} label={item.label} />
          ))}
        </nav>

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label="Open command palette"
          className="ml-auto flex h-[30px] w-[330px] items-center gap-2 rounded-lg bg-[var(--s0)] px-2.5 text-left text-muted-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,.6)] transition-colors hover:text-foreground"
        >
          <span className="flex-1 truncate text-body">Search datasets, run a command…</span>
          <kbd className="rounded bg-secondary px-[5px] py-[1.5px] font-mono text-micro text-foreground shadow-[var(--hi)]">
            ⌘K
          </kbd>
        </button>

        <div className="flex shrink-0 items-center gap-1">
          <IconButton label="Alerts" onClick={() => setPaletteOpen(true)}>
            <Bell className="size-3.5" />
          </IconButton>
          <IconButton label="Toggle theme" onClick={toggleTheme}>
            {resolved === 'dark' ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
          </IconButton>
        </div>

        <SeatSwitcher />
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </div>

      <StudioCommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  )
}
