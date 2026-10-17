/**
 * The shared top-bar pieces (DESIGN §1, §3): every page's 48px bar is a `TopBarFrame` (glass chrome on the
 * canvas, no outline) with the Command Studio brand on the left. Projects home, the workspace, the
 * workflows pages and the app builder all use it, so the bar never jumps between pages.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Tip } from '@/shared/components/ui/tooltip'
import { useTheme } from '@/shared/components/theme-provider'
import { cn } from '@/shared/lib/utils'

export function useToggleTheme() {
  const { theme, setTheme } = useTheme()
  const isDark =
    theme === 'dark' || (theme === 'system' && typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  return { isDark, toggle: () => setTheme(isDark ? 'light' : 'dark') }
}

export function ThemeToggle() {
  const { isDark, toggle } = useToggleTheme()
  return (
    <Tip label={isDark ? 'Light theme' : 'Dark theme'}>
      <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label="Toggle theme" data-testid="theme-toggle">
        {isDark ? <Moon /> : <Sun />}
      </Button>
    </Tip>
  )
}

/** The logo tile: a white `>_` on the steel-blue → cyan gradient. */
export function BrandMark({ className }: { className?: string }) {
  const id = `cs-brand-${useId().replace(/:/g, '')}`
  return (
    <svg viewBox="0 0 24 24" className={cn('size-[22px] shrink-0 rounded-ctl text-primary-foreground shadow-raise-sm', className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--brand-1)" />
          <stop offset="1" stopColor="var(--brand-2)" />
        </linearGradient>
      </defs>
      <rect width="24" height="24" rx="7" fill={`url(#${id})`} />
      <path d="M7.6 8.2 11.4 12l-3.8 3.8" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.6 15.8h3.2" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  )
}

/** "Command Studio" wordmark ("Studio" lighter), linking home. */
export function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link
      to="/projects"
      className="inline-flex h-7 shrink-0 items-center gap-2 rounded-ctl pr-2 pl-0.5 text-[14.5px] leading-none font-[650] tracking-[-0.025em] text-foreground hover:bg-(--hover-fill)"
      data-testid="brand"
      aria-label="Command Studio"
    >
      <BrandMark />
      {!compact && (
        <span className="whitespace-nowrap">
          Command <span className="font-[450] text-foreground/75">Studio</span>
        </span>
      )}
    </Link>
  )
}

/** The `/` between the brand and the crumb. */
export function CrumbSep() {
  return (
    <span className="mx-0.5 text-base text-faint select-none" aria-hidden>
      /
    </span>
  )
}

/** The nearest ancestor that scrolls vertically (or null for the window). */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let p = el?.parentElement ?? null; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY
    if (/(auto|scroll)/.test(oy)) return p
  }
  return null
}

/**
 * The 48px bar itself. `absolute` (the workspace) sits on the canvas. `sticky` (Projects home, landing) is
 * flat at the top of the page and frosts — glass fill, blur, a hairline — once content scrolls under it.
 */
export function TopBarFrame({
  children,
  className,
  position = 'absolute',
  ...props
}: React.HTMLAttributes<HTMLElement> & { position?: 'absolute' | 'sticky' }) {
  const ref = useRef<HTMLElement>(null)
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    if (position !== 'sticky') return
    const host = scrollParent(ref.current)
    const target: HTMLElement | Window = host ?? window
    const read = () => setScrolled((host ? host.scrollTop : window.scrollY) > 4)
    read()
    target.addEventListener('scroll', read, { passive: true })
    return () => target.removeEventListener('scroll', read)
  }, [position])
  return (
    <header
      ref={ref}
      data-scrolled={position === 'sticky' ? (scrolled ? '' : undefined) : undefined}
      className={cn(
        'z-20 flex h-12 items-center gap-1.5 pr-3 pl-3.5',
        position === 'absolute' ? 'absolute inset-x-0 top-0 glass-chrome' : 'sticky top-0 transition-[background-color,box-shadow,backdrop-filter] duration-(--d-base) ease-(--ease-out)',
        position === 'sticky' && scrolled && 'bg-(--overlay-fill) shadow-[inset_0_-1px_0_var(--hairline)] backdrop-blur-[20px] backdrop-saturate-[1.1]',
        className,
      )}
      {...props}
    >
      {children}
    </header>
  )
}

/** The recessed "Search or jump to…" trigger with a raised ⌘K. */
export function SearchTrigger({ onClick, label = 'Search or jump to…', testId = 'open-palette' }: { onClick: () => void; label?: string; testId?: string }) {
  return (
    <Tip label="Search projects, routes and commands">
      <button
        type="button"
        onClick={onClick}
        data-testid={testId}
        data-recess
        className="hidden h-[30px] w-60 items-center gap-2 rounded-ctl bg-recess pr-1.5 pl-2.5 text-[12.5px] text-muted-foreground hover:bg-(--hover-fill) md:inline-flex"
      >
        <svg viewBox="0 0 24 24" className="size-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden>
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <span className="hidden flex-1 truncate text-left lg:inline">{label}</span>
        <kbd className="ml-auto inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-kbd bg-raise px-1 text-[10.5px] font-medium leading-none text-muted-foreground shadow-raise-sm">
          ⌘K
        </kbd>
      </button>
    </Tip>
  )
}
