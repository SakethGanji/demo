/**
 * The landing page (`/`) is the "One Engine, Many Uses" film, told by scrolling: each scroll step snaps to the next
 * part (the engine, what it connects to, how it stays safe, what is built on it, the notebook, keeping an answer,
 * the testers, the recap), and that part plays itself, then rests on its last frame. The last stop hands over to the
 * product: open the latest workflow, or any part of the studio.
 *
 * The film is its own page (`public/landing-film.html`, the presenter film without its controls) in a same-origin
 * frame that ignores the pointer; this page drives it through `window.film` (play a scene, follow the theme).
 * Keys: ↓ → Space PageDown for the next part, ↑ ← PageUp for the one before.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { motion } from 'motion/react'
import { AppWindow, ArrowRight, ChevronDown, FolderKanban, GitCompareArrows, NotebookPen } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { Button } from '@/shared/components/ui/button'
import { ThemeToggle } from '@/features/project/components/TopBar'
import { useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { useMotionOff } from '@/features/knowledge/hooks/useMotionOff'

const P = 'default'
const EASE = [0.22, 1, 0.36, 1] as const

/** the film's scenes, in order, as the rail names them */
const PARTS = ['Where it started', 'The engine', 'Connections', 'Safety', 'Macro elements', 'Use case 1 · data analysis', 'Keep it running', 'Use case 2 · testing', 'Recap']
const STOPS = PARTS.length + 1

type Film = {
  play: (i: number) => void
  rest: (i: number) => void
  quiet: (on: boolean) => void
  theme: (dark: boolean) => void
  pointer: (x: number, y: number) => void
}

export default function LandingPage() {
  const still = useMotionOff()
  const scroller = useRef<HTMLDivElement>(null)
  const frame = useRef<HTMLIFrameElement>(null)
  const film = useRef<Film | null>(null)
  const [active, setActive] = useState(0)
  const atEnd = active === STOPS - 1

  // the newest workflow in the default project: the tab opens it directly
  const { data: flows } = useProjectWorkflows(P)
  const latest = useMemo(() => [...(flows ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0], [flows])
  const workflowLink = latest
    ? ({ to: '/p/$project/workflows/$workflowId', params: { project: P, workflowId: latest.id } } as const)
    : ({ to: '/p/$project/workflows', params: { project: P } } as const)

  // the part in view: the one that fills most of the screen
  useEffect(() => {
    const root = scroller.current
    if (!root) return
    const io = new IntersectionObserver(
      (es) => es.forEach((e) => e.isIntersecting && setActive(Number((e.target as HTMLElement).dataset.stop))),
      { root, threshold: 0.55 },
    )
    root.querySelectorAll('[data-stop]').forEach((s) => io.observe(s))
    return () => io.disconnect()
  }, [])

  // play the part in view (the opening plays on its own as the film loads)
  const shown = useRef(0)
  useEffect(() => {
    film.current?.quiet(atEnd)
    if (active === shown.current || active >= PARTS.length) return
    shown.current = active
    film.current?.play(active)
  }, [active, atEnd])

  // keep the film's theme in step with the studio's
  useEffect(() => {
    const sync = () => {
      const dark = document.documentElement.classList.contains('dark')
      film.current?.theme(dark)
    }
    const mo = new MutationObserver(sync)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })
    return () => mo.disconnect()
  }, [])

  // the frame ignores the pointer, so pass it in: the drawing's camera leans toward it
  useEffect(() => {
    if (still) return
    const onMove = (e: PointerEvent) => film.current?.pointer((e.clientX / innerWidth) * 2 - 1, (e.clientY / innerHeight) * 2 - 1)
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => window.removeEventListener('pointermove', onMove)
  }, [still])

  const onFilmLoad = () => {
    const f = (frame.current?.contentWindow as (Window & { film?: Film }) | null)?.film
    if (!f) return
    film.current = f
    f.theme(document.documentElement.classList.contains('dark'))
    if (shown.current > 0 && shown.current < PARTS.length) f.play(shown.current)
  }

  const go = useCallback((i: number) => {
    const el = scroller.current?.querySelector<HTMLElement>(`[data-stop="${Math.max(0, Math.min(STOPS - 1, i))}"]`)
    el?.scrollIntoView({ behavior: still ? 'auto' : 'smooth' })
  }, [still])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.metaKey || e.ctrlKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      // Space on a focused button or link presses it
      if (e.key === ' ' && t && /^(BUTTON|A)$/.test(t.tagName)) return
      const next = ['ArrowDown', 'ArrowRight', 'PageDown', ' '].includes(e.key) && !e.shiftKey
      const prev = ['ArrowUp', 'ArrowLeft', 'PageUp'].includes(e.key) || (e.key === ' ' && e.shiftKey)
      if (!next && !prev) return
      e.preventDefault()
      go(active + (next ? 1 : -1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, go])

  return (
    <div className="relative h-screen w-full overflow-hidden" data-testid="landing-page">
      {/* the film, full screen with no top bar; at the last stop it quiets behind the hand-over */}
      <iframe
        ref={frame}
        src="/landing-film.html"
        title="One Engine, Many Uses"
        tabIndex={-1}
        onLoad={onFilmLoad}
        className="pointer-events-none absolute inset-0 z-[1] h-full w-full border-0"
        data-testid="landing-film"
      />

      {/* no top bar: only the theme switch floats in the corner */}
      <div className="glass-strip absolute top-4 right-4 z-30 rounded-full p-0.5" data-testid="landing-theme">
        <ThemeToggle />
      </div>

      {/* the stops: one per part of the film, then the hand-over; each is a full screen that snaps */}
      <div ref={scroller} className="absolute inset-0 z-10 snap-y snap-mandatory overflow-y-auto overscroll-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" data-testid="landing-scroller">
        {PARTS.map((name, i) => (
          <section key={name} data-stop={i} className="h-screen snap-start snap-always" aria-label={name} data-testid={`landing-part-${i}`} />
        ))}

        <section data-stop={STOPS - 1} className="relative flex h-screen snap-start snap-always items-center justify-center px-4 sm:px-8" data-testid="landing-end">
          <motion.div
            className="flex max-w-[52rem] flex-col items-center gap-8 text-center"
            initial={false}
            animate={atEnd || still ? { opacity: 1, y: 0 } : { opacity: 0, y: 24 }}
            transition={{ duration: 0.7, ease: EASE, delay: atEnd ? 0.25 : 0 }}
          >
            <h1 className="text-[44px] leading-[1.02] font-bold tracking-[-0.045em] text-balance text-foreground sm:text-[80px]">
              Now, the real thing.
            </h1>
            <p className="max-w-[34rem] text-[16px] leading-relaxed text-muted-foreground">
              It started as a workflow builder. Open a workflow and see where it began, then everything built on the same engine.
            </p>
            <Button size="lg" className="group h-12 px-6 text-[15px]" render={<Link {...workflowLink} />} data-testid="landing-get-started">
              {latest ? `Open ${latest.name}` : 'Open the workflow builder'}
              <ArrowRight className="transition-transform duration-300 group-hover:translate-x-0.5" />
            </Button>
            <div className="grid w-full gap-3 sm:grid-cols-4">
              {(
                [
                  { label: 'Workflows', icon: GitCompareArrows, to: '/p/$project/workflows' },
                  { label: 'Notebook', icon: NotebookPen, to: '/p/$project/notebooks' },
                  { label: 'App builder', icon: AppWindow, to: '/p/$project/apps' },
                ] as const
              ).map((l) => (
                <Link key={l.label} to={l.to} params={{ project: P }} className="tile group flex items-center gap-3 px-4 py-3.5 text-left transition-transform duration-300 hover:-translate-y-0.5">
                  <l.icon className="size-5 text-primary" />
                  <span className="flex-1 text-[14.5px] font-semibold text-foreground">{l.label}</span>
                  <ArrowRight className="size-4 text-muted-foreground transition-transform duration-300 group-hover:translate-x-0.5" />
                </Link>
              ))}
              <Link to="/projects" className="tile group flex items-center gap-3 px-4 py-3.5 text-left transition-transform duration-300 hover:-translate-y-0.5">
                <FolderKanban className="size-5 text-primary" />
                <span className="flex-1 text-[14.5px] font-semibold text-foreground">Projects</span>
                <ArrowRight className="size-4 text-muted-foreground transition-transform duration-300 group-hover:translate-x-0.5" />
              </Link>
            </div>
          </motion.div>
        </section>
      </div>

      {/* the chapter rail: where you are, and a jump to any part (names on hover only; the top bar names the part in view) */}
      <nav className="absolute top-1/2 right-4 z-20 hidden -translate-y-1/2 flex-col items-end gap-2.5 lg:flex" aria-label="Parts" data-testid="landing-rail">
        {[...PARTS, 'Open the studio'].map((name, i) => (
          <button key={name} type="button" onClick={() => go(i)} className="group flex items-center gap-2.5" aria-current={active === i ? 'step' : undefined}>
            <span
              className={cn(
                'rounded-full bg-background/70 px-2 py-0.5 text-[11.5px] font-medium whitespace-nowrap text-foreground opacity-0 shadow-sm backdrop-blur transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100',
              )}
            >
              {name}
            </span>
            <span className={cn('block rounded-full transition-all duration-300', active === i ? 'h-6 w-1.5 bg-primary' : 'size-1.5 bg-foreground/35 group-hover:bg-foreground/60')} />
          </button>
        ))}
      </nav>

      {/* the first stop's hint */}
      <motion.button
        type="button"
        onClick={() => go(1)}
        className="absolute bottom-6 left-1/2 z-20 flex -translate-x-1/2 flex-col items-center gap-1 text-[12px] font-medium text-muted-foreground"
        initial={false}
        animate={{ opacity: active === 0 ? 1 : 0, y: active === 0 ? 0 : 8 }}
        transition={{ duration: 0.4 }}
        style={{ pointerEvents: active === 0 ? 'auto' : 'none' }}
        data-testid="landing-scroll-hint"
      >
        Scroll
        <ChevronDown className={cn('size-4', !still && 'animate-bounce')} />
      </motion.button>
    </div>
  )
}
