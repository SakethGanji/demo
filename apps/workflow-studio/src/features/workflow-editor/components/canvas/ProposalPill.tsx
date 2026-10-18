import { AnimatePresence, motion } from 'motion/react'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
import { dur, ease, spring } from '@/shared/motion/tokens'
import { Roll } from '@/shared/components/ui/roll'

const btn = 'rounded-full px-2.5 py-1 text-xs font-medium transition-colors duration-(--d-base) ease-(--ease-out)'

/** Click the sidebar control: Keep needs the name field and confirmation that live in the review panel. */
function clickPanel(testid: string) {
  document.querySelector<HTMLElement>(`[data-testid="${testid}"]`)?.click()
}

/**
 * Top-centre pill over the canvas while an AI stage is active. Building: what is happening + Skip. Review: "Previewing
 * AI change" + Keep / Discard (they trigger the review panel's own buttons). A Follow button appears once the user pans
 * or zooms away from the camera.
 */
export default function ProposalPill({ inset }: { inset: number }) {
  const active = useStageStore((s) => s.active)
  const mode = useStageStore((s) => s.mode)
  const playback = useStageStore((s) => s.playback)
  const follow = useStageStore((s) => s.follow)
  const requestSkip = useStageStore((s) => s.requestSkip)
  const setFollow = useStageStore((s) => s.setFollow)
  const progress = useStageStore((s) => s.progress)
  const showProgress = mode === 'building' && !!progress && progress.total > 0

  const text =
    mode === 'review' ? 'Previewing AI change'
    : mode === 'settling' ? 'Applying the change'
    : mode === 'reversing' ? 'Undoing the change'
    : mode === 'blueprint' ? 'AI is planning'
    : 'AI is building'

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="stage-pill"
          data-testid="ai-stage-pill"
          data-mode={mode}
          className="pointer-events-auto absolute top-3 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-raise py-1 pl-3 pr-1.5 text-xs text-foreground shadow-raise-md"
          style={{ left: `calc((100% - ${inset}px) / 2)`, willChange: 'transform, opacity' }}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: dur.base, ease: ease.out }}
        >
          <span className="mr-1 font-medium" data-testid="ai-stage-pill-text">{text}</span>
          {showProgress && progress && (
            <span
              data-testid="ai-stage-progress"
              data-done={progress.done}
              data-total={progress.total}
              className="mr-1 inline-flex items-center font-mono text-[11px] tabular-nums text-muted-foreground"
            >
              <Roll value={progress.done} />/{progress.total}
            </span>
          )}
          {(mode === 'building' || mode === 'blueprint') && playback === 'playing' && (
            <button type="button" data-testid="ai-build-skip" className={`${btn} text-muted-foreground hover:bg-recess hover:text-foreground`} onClick={requestSkip}>
              Skip
            </button>
          )}
          {!follow && (mode === 'building' || mode === 'review') && (
            <button type="button" data-testid="ai-stage-follow" className={`${btn} text-primary hover:bg-recess`} onClick={() => setFollow(true)}>
              Follow
            </button>
          )}
          {mode === 'review' && (
            <>
              <button type="button" data-testid="ai-stage-keep" className={`${btn} bg-primary text-primary-foreground`} onClick={() => clickPanel('ai-keep')}>
                Keep
              </button>
              <button type="button" data-testid="ai-stage-discard" className={`${btn} text-muted-foreground hover:bg-recess hover:text-foreground`} onClick={() => clickPanel('ai-discard')}>
                Discard
              </button>
            </>
          )}
          {showProgress && progress && (
            <span className="pointer-events-none absolute inset-x-3 bottom-[3px] h-0.5 rounded-full bg-recess" aria-hidden>
              <motion.span
                className="block h-full origin-left rounded-full bg-primary"
                initial={false}
                animate={{ scaleX: Math.min(1, progress.done / progress.total) }}
                transition={spring.firm}
              />
            </span>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  )
}
