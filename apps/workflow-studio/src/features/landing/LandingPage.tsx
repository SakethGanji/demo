/**
 * The landing page (`/`): a drifting aurora behind real frosted glass, the engine shown as a 3D hero shot that
 * straightens as you scroll and pages (arrows, dots, keys, autoplay) through workflow / code / UI views and,
 * last, an AI build replay, under two quiet buzzword strips. The visuals carry it; the words are a word each.
 * The landing scopes its own glass in index.css (`body:has([data-testid="landing-page"])`): clear glass, no blur
 * (a blur over the live shader would re-blur every pane every frame).
 *
 * Motion budget: everything here animates transform/opacity only; scroll drives a spring (not the raw value), the
 * pointer drives springs, and with motion off every piece shows its still, finished state.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  AnimatePresence,
  motion,
  useAnimationFrame,
  useMotionValue,
  useScroll,
  useSpring,
  useTransform,
  type MotionValue,
  type Variants,
} from 'motion/react'
import { ArrowRight, ArrowUpRight, ChevronLeft, ChevronRight, FlaskConical, History, Lock, Sparkles, Zap } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { Button } from '@/shared/components/ui/button'
import { Tile } from '@/shared/components/ui/surface'
import { HomeSky } from '@/shared/components/space/HomeSky'
import { SignalField } from '@/shared/components/backgrounds/SignalField'
import { Brand, ThemeToggle, TopBarFrame } from '@/features/project/components/TopBar'
import { useMotionOff } from '@/features/knowledge/hooks/useMotionOff'
import { BuildMock } from './BuildMock'
import { ShowcaseView } from './Showcase'

const newFlow = { to: '/p/$project/workflows/$workflowId', params: { project: 'default', workflowId: 'new' } } as const
const EASE = [0.22, 1, 0.36, 1] as const
/** scroll px over which the hero panel straightens */
const SETTLE = 420
/** a soft spring for anything that follows the pointer or the scroll */
const FOLLOW = { stiffness: 150, damping: 22, mass: 0.5 }

const BUZZ_A = ['Durable', 'Typed', 'Versioned', 'Observable', 'Event‑driven', 'Self‑testing']
const BUZZ_B = ['API‑first', 'Secret‑safe', 'Full‑stack', 'Low‑code', 'Pro‑code', 'Agentic']

function Reveal({ children, className, delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  const still = useMotionOff()
  return (
    <motion.div
      className={className}
      initial={still ? false : { opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.8, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

/** The headline, word by word: each rises out of its own line mask (transform only, no blur). */
function Kinetic({ text, className, delay = 0 }: { text: string; className?: string; delay?: number }) {
  const still = useMotionOff()
  return (
    <span className={className}>
      {text.split(' ').map((w, i) => (
        <span key={i} className="-mb-[0.14em] inline-block overflow-hidden pb-[0.14em] align-top">
          <motion.span
            className="inline-block whitespace-pre"
            initial={still ? false : { y: '108%' }}
            animate={{ y: '0%' }}
            transition={{ duration: 0.95, delay: delay + i * 0.08, ease: EASE }}
          >
            {w}{' '}
          </motion.span>
        </span>
      ))}
    </span>
  )
}

/** Pulls its child a little toward the pointer, then springs back. */
function Magnetic({ children, strength = 0.25 }: { children: React.ReactNode; strength?: number }) {
  const still = useMotionOff()
  const x = useSpring(0, FOLLOW)
  const y = useSpring(0, FOLLOW)
  return (
    <motion.div
      className="inline-flex"
      style={{ x, y }}
      onPointerMove={(e) => {
        if (still || e.pointerType !== 'mouse') return
        const r = e.currentTarget.getBoundingClientRect()
        x.set((e.clientX - r.left - r.width / 2) * strength)
        y.set((e.clientY - r.top - r.height / 2) * strength)
      }}
      onPointerLeave={() => {
        x.set(0)
        y.set(0)
      }}
    >
      {children}
    </motion.div>
  )
}

/** A small glass chip beside the hero window: pops in, bobs on the compositor (CSS), and drifts with the scroll for depth. */
function FloatChip({ className, children, dur, delay, drift, depth }: { className: string; children: React.ReactNode; dur: number; delay: number; drift: MotionValue<number>; depth: number }) {
  const still = useMotionOff()
  const y = useTransform(drift, (v) => (still ? 0 : v * depth))
  return (
    <motion.div
      className={cn('absolute z-20 hidden lg:block', className)}
      style={{ y }}
      initial={still ? false : { opacity: 0, scale: 0.85 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: 'spring', stiffness: 260, damping: 22, delay }}
      aria-hidden
    >
      <div
        className={cn('tile flex items-center gap-2 rounded-full! px-3.5 py-2 text-[12.5px] font-semibold whitespace-nowrap', !still && 'landing-float')}
        style={{ animationDuration: `${dur}s`, animationDelay: `${delay}s` }}
      >
        {children}
      </div>
    </motion.div>
  )
}

/** The hero panel's pages: a workflow built on the canvas first, then code and UI. `dwell` is the autoplay time (ms). */
const SLIDES = [
  { key: 'build', title: 'New workflow · Build', dwell: 11800 },
  { key: 'code', title: 'Code, generated', dwell: 8000 },
  { key: 'ui', title: 'UI, generated', dwell: 7000 },
]
const L = SLIDES.length

const pageVariants: Variants = {
  enter: (d: number) => ({ opacity: 0, x: d * 56 }),
  center: {
    opacity: 1,
    x: 0,
    transition: { x: { type: 'spring', stiffness: 360, damping: 34, mass: 0.7 }, opacity: { duration: 0.22, ease: 'easeOut' } },
  },
  exit: (d: number) => ({ opacity: 0, x: d * -56, transition: { duration: 0.16, ease: 'easeIn' } }),
}

function SideArrow({ side, show, onClick }: { side: 'left' | 'right'; show: boolean; onClick: () => void }) {
  const still = useMotionOff()
  const Icon = side === 'left' ? ChevronLeft : ChevronRight
  const x = useSpring(0, FOLLOW)
  const y = useSpring(0, FOLLOW)
  return (
    <motion.button
      type="button"
      aria-label={side === 'left' ? 'Previous' : 'Next'}
      onClick={onClick}
      className={cn('tile group absolute top-1/2 z-30 hidden size-12 -translate-y-1/2 items-center justify-center rounded-full! text-foreground lg:flex', side === 'left' ? '-left-20' : '-right-20')}
      style={{ x, y }}
      initial={still ? false : { opacity: 0, scale: 0.8 }}
      animate={{ opacity: show ? 1 : 0, scale: show ? 1 : 0.8 }}
      transition={{ type: 'spring', stiffness: 300, damping: 24 }}
      whileHover={{ scale: 1.08 }}
      whileTap={{ scale: 0.92 }}
      onPointerMove={(e) => {
        if (still || e.pointerType !== 'mouse') return
        const r = e.currentTarget.getBoundingClientRect()
        x.set((e.clientX - r.left - r.width / 2) * 0.35)
        y.set((e.clientY - r.top - r.height / 2) * 0.35)
      }}
      onPointerLeave={() => {
        x.set(0)
        y.set(0)
      }}
      data-testid={`landing-hero-${side === 'left' ? 'prev' : 'next'}`}
    >
      <Icon className={cn('size-5 transition-transform duration-300', side === 'left' ? 'group-hover:-translate-x-0.5' : 'group-hover:translate-x-0.5')} />
    </motion.button>
  )
}

function BuzzStrip({ words, reverse }: { words: string[]; reverse?: boolean }) {
  return (
    <div className="landing-marquee-mask overflow-hidden" aria-hidden>
      <div className={cn('landing-marquee flex w-max items-center gap-6 py-1', reverse && 'landing-marquee-rev')}>
        {[...words, ...words].map((w, i) => (
          <span key={i} className="flex items-center gap-6">
            <span className={cn('text-[22px] leading-none font-[650] tracking-[-0.03em] sm:text-[28px]', i % 2 ? 'landing-outline' : 'text-muted-foreground')}>{w}</span>
            <Sparkles className="size-4 shrink-0 text-muted-foreground/60" />
          </span>
        ))}
      </div>
    </div>
  )
}

/** One pager dot; the active one fills as autoplay runs toward the next slide. */
function Dot({ title, testId, active, progress, still, onClick }: { title: string; testId: string; active: boolean; progress: MotionValue<number>; still: boolean; onClick: () => void }) {
  const x = useTransform(progress, (p) => `${(Math.min(1, p) - 1) * 100}%`)
  return (
    <button
      type="button"
      aria-label={title}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'relative h-2 overflow-hidden rounded-full transition-[width,background-color] duration-300 ease-out',
        active ? 'w-8 bg-foreground/20' : 'w-2 bg-foreground/25 hover:bg-foreground/45',
      )}
      data-testid={testId}
    >
      {active && <motion.span className="absolute inset-0 rounded-full bg-foreground" style={{ x: still ? '0%' : x }} />}
    </button>
  )
}

export default function LandingPage() {
  const root = useRef<HTMLDivElement>(null)
  const still = useMotionOff()

  // the tilt eases toward the scroll (a spring), so it glides rather than tracking the wheel 1:1
  const { scrollY } = useScroll({ container: root })
  const eased = useSpring(scrollY, { stiffness: 120, damping: 24, mass: 0.6 })
  const tilt = useTransform(eased, [0, SETTLE], [still ? 0 : 26, 0])
  const scale = useTransform(eased, [0, SETTLE], [still ? 1 : 0.9, 1])
  const lift = useTransform(eased, [0, SETTLE], [still ? 0 : 40, 0])
  const drift = useTransform(eased, [0, SETTLE], [1, 0])

  // the pointer over the panel: a light sheen follows it, and the glass leans a degree or two toward it
  const px = useMotionValue(0)
  const py = useMotionValue(0)
  const sheenX = useSpring(0, FOLLOW)
  const sheenY = useSpring(0, FOLLOW)
  const sheenO = useSpring(0, { stiffness: 200, damping: 30 })
  const leanX = useSpring(px, FOLLOW)
  const leanY = useSpring(py, FOLLOW)
  const rotateX = useTransform(() => tilt.get() - leanY.get() * 2)
  const rotateY = useTransform(() => leanX.get() * 2.5)
  const panel = useRef<HTMLDivElement>(null)
  const hovering = useRef(false)

  // paging: [slide, direction]; autoplay fills `progress` while the panel is not hovered
  const [[slide, dir], setPage] = useState<[number, number]>([0, 1])
  const progress = useMotionValue(0)
  const go = useCallback(
    (to: (s: number) => number) => {
      setPage(([s]) => {
        const next = (to(s) + L) % L
        const d = to(s) > s ? 1 : -1
        return [next, d]
      })
      progress.set(0)
    },
    [progress],
  )
  const step = useCallback((d: number) => go((s) => s + d), [go])

  // the arrows and dots arrive just after the intro; autoplay starts with them
  const [ready, setReady] = useState(false)
  useEffect(() => {
    const id = window.setTimeout(() => setReady(true), still ? 0 : 1400)
    return () => window.clearTimeout(id)
  }, [still])
  const shown = still || ready

  useAnimationFrame((_, delta) => {
    if (!shown || still || hovering.current || document.hidden) return
    const p = progress.get() + delta / SLIDES[slide].dwell
    if (p >= 1) step(1)
    else progress.set(p)
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      if (e.key === 'ArrowRight') step(1)
      else if (e.key === 'ArrowLeft') step(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step])

  const onPanelMove = (e: React.PointerEvent) => {
    if (still || e.pointerType !== 'mouse' || !panel.current) return
    const r = panel.current.getBoundingClientRect()
    const lx = e.clientX - r.left
    const ly = e.clientY - r.top
    sheenX.set(lx)
    sheenY.set(ly)
    px.set((lx / r.width) * 2 - 1)
    py.set((ly / r.height) * 2 - 1)
  }
  const onPanelEnter = () => {
    hovering.current = true
    if (!still) sheenO.set(1)
  }
  const onPanelLeave = () => {
    hovering.current = false
    sheenO.set(0)
    px.set(0)
    py.set(0)
  }

  const current = SLIDES[slide]

  return (
    <div ref={root} className="relative h-screen w-full overflow-x-hidden overflow-y-auto" data-testid="landing-page">
      <div className="pointer-events-none fixed inset-0">
        <SignalField />
      </div>
      <HomeSky />
      <TopBarFrame position="sticky" className="gap-2">
        <Brand />
        <div className="flex-1" />
        <Button variant="ghost" size="sm" render={<Link to="/projects" />} data-testid="landing-projects-link">
          Projects
        </Button>
        <Button variant="ghost" size="sm" render={<Link to="/knowledge" />} data-testid="landing-knowledge-link">
          Knowledge
        </Button>
        <Button variant="ghost" size="sm" render={<Link {...newFlow} />} data-testid="landing-editor-link" className="max-sm:hidden">
          Editor
        </Button>
        <ThemeToggle />
      </TopBarFrame>

      {/* min height: always leaves enough scroll for the panel to straighten fully, even on a tall screen */}
      <main className="relative z-10 flex min-h-[calc(100vh+28rem)] w-full flex-col gap-32 pt-6 pb-20 sm:pt-8">
        {/* Buzzwords */}
        <section className="-mb-24 flex flex-col gap-1 opacity-70 sm:-mb-20">
          <BuzzStrip words={BUZZ_A} />
          <BuzzStrip words={BUZZ_B} reverse />
        </section>

        {/* Hero */}
        <section className="mx-auto flex w-full max-w-[74rem] flex-col items-center gap-12 px-4 sm:px-8" data-testid="landing-hero">
          <div className="flex flex-col items-center gap-6 text-center">
            <h1 className="text-[46px] leading-[1.0] font-[720] tracking-[-0.045em] sm:text-[84px]">
              <Kinetic text={'One engine.'} />
              <br />
              <Kinetic text={'Every workload.'} delay={0.14} />
              <br />
              <Kinetic text={'Ship‑ready.'} delay={0.3} />
            </h1>
            <Reveal delay={0.55}>
              <p className="max-w-[32rem] text-[16px] leading-relaxed text-muted-foreground">Workflows, APIs, data and apps on one engine. AI helps you build; the engine runs it.</p>
            </Reveal>
            <Reveal delay={0.65} className="flex flex-wrap items-center justify-center gap-3">
              <Magnetic>
                <Button size="lg" className="group h-11 px-5 text-[14px]" render={<Link to="/projects" />} data-testid="landing-get-started">
                  Get started <ArrowRight className="transition-transform duration-300 group-hover:translate-x-0.5" />
                </Button>
              </Magnetic>
              <Magnetic>
                <Button size="lg" variant="outline" className="group h-11 px-5 text-[14px]" render={<Link {...newFlow} />} data-testid="landing-new-flow">
                  Create new flow <ArrowUpRight className="transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                </Button>
              </Magnetic>
            </Reveal>
          </div>

          <div className="relative w-full [perspective:1600px]">
            <FloatChip className="-top-5 -left-6 text-success-text" dur={5} delay={1.2} drift={drift} depth={-28}>
              <FlaskConical className="size-4" /> 6/6 tests passed
            </FloatChip>
            <FloatChip className="-top-5 -right-4" dur={6} delay={1.35} drift={drift} depth={-44}>
              <Zap className="size-4 text-hue-runs" /> p50 84ms
            </FloatChip>
            <FloatChip className="bottom-16 -left-12" dur={5.5} delay={1.5} drift={drift} depth={36}>
              <Lock className="size-4 text-hue-secrets" /> 0 secrets in code
            </FloatChip>
            <FloatChip className="-right-4 -bottom-5" dur={6.5} delay={1.65} drift={drift} depth={52}>
              <History className="size-4 text-primary" /> {'Versioned · instant rollback'}
            </FloatChip>
            <SideArrow side="left" show={shown} onClick={() => step(-1)} />
            <SideArrow side="right" show={shown} onClick={() => step(1)} />
            <motion.div
              ref={panel}
              // its own layer, rastered once: without will-change a changing scale/3D tilt re-rasters the whole panel every frame
              style={{ rotateX, rotateY, scale, y: lift, transformOrigin: '50% 0%', willChange: 'transform' }}
              initial={still ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.9, delay: 0.4 }}
              onPointerEnter={onPanelEnter}
              onPointerMove={onPanelMove}
              onPointerLeave={onPanelLeave}
            >
              <div className="landing-halo rounded-panel">
                <span className="landing-halo-ring" aria-hidden>
                  <span />
                </span>
                <Tile className="relative w-full overflow-hidden">
                  <div className="flex h-10 items-center gap-2 border-b border-hairline px-4">
                    <span className="size-2.5 rounded-full bg-foreground/15" />
                    <span className="size-2.5 rounded-full bg-foreground/15" />
                    <span className="size-2.5 rounded-full bg-foreground/15" />
                    <AnimatePresence mode="wait" initial={false}>
                      <motion.span
                        key={current.key}
                        className="ml-3 truncate text-[12px] font-medium text-muted-foreground"
                        initial={still ? false : { opacity: 0, y: 5 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={still ? undefined : { opacity: 0, y: -5 }}
                        transition={{ duration: 0.16 }}
                      >
                        {current.title}
                      </motion.span>
                    </AnimatePresence>
                    {current.key === 'build' && (
                      <span className="ml-auto inline-flex h-5 items-center gap-1 rounded-full bg-ai-soft px-2 text-[11px] font-semibold text-ai-text">
                        <Sparkles className="size-3" /> AI
                      </span>
                    )}
                  </div>
                  <div className="grid min-h-[20rem]">
                    <AnimatePresence mode="wait" initial={false} custom={dir}>
                      <motion.div
                        key={current.key}
                        className="flex items-center"
                        custom={dir}
                        variants={pageVariants}
                        initial={still ? false : 'enter'}
                        animate="center"
                        exit={still ? undefined : 'exit'}
                      >
                        {current.key === 'build' ? (
                          <div className="w-full"><BuildMock /></div>
                        ) : (
                          <div className="w-full p-3 sm:p-6">
                            <ShowcaseView which={current.key} still={still} />
                          </div>
                        )}
                      </motion.div>
                    </AnimatePresence>
                  </div>
                  <motion.span className="landing-sheen" style={{ x: sheenX, y: sheenY, opacity: sheenO }} aria-hidden />
                </Tile>
              </div>
            </motion.div>
            <motion.div
              className="mt-5 flex items-center justify-center gap-2"
              initial={still ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: shown ? 1 : 0, y: shown ? 0 : 6 }}
              transition={{ duration: 0.5, ease: EASE }}
            >
              {SLIDES.map((sl, i) => (
                <Dot
                  key={sl.key}
                  title={sl.title}
                  testId={`landing-hero-dot-${sl.key}`}
                  active={slide === i}
                  progress={progress}
                  still={still}
                  onClick={() => go((s) => (i === s ? s : i))}
                />
              ))}
            </motion.div>
          </div>
        </section>

        <footer className="mt-auto flex flex-col items-center gap-2 text-center" data-testid="landing-footer">
          <span className="text-[56px] leading-none font-[720] tracking-[-0.05em] text-foreground/12 select-none sm:text-[120px]">Command Studio</span>
          <span className="text-[12px] text-muted-foreground">One engine. Every workload.</span>
        </footer>
      </main>
    </div>
  )
}
