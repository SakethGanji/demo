import { motion } from 'motion/react';
import { breathe, dur, ease } from '@/shared/motion/tokens';
import { Elapsed } from './Elapsed';

const DOTS = [0, 1, 2];

/** A clean sweep of the foreground colour across muted text: the letters never go missing, only brighten as the band passes. */
function SweepText({ children }: { children: string }) {
  return (
    <motion.span
      className="inline-block bg-clip-text text-[13px] font-medium text-transparent [background-repeat:no-repeat]" // design-ok: text sweep (bg-clip-text), not a box fill
      style={{
        backgroundImage: 'linear-gradient(90deg, var(--color-muted-foreground) 0%, var(--color-muted-foreground) 35%, var(--color-foreground) 50%, var(--color-muted-foreground) 65%, var(--color-muted-foreground) 100%)', // design-ok: text sweep
        backgroundSize: '300% 100%',
      }}
      initial={{ backgroundPosition: '100% 0%' }}
      animate={{ backgroundPosition: '0% 0%' }}
      transition={{ duration: 2.2, ease: 'linear', repeat: Infinity }}
    >{children}</motion.span>
  );
}

/**
 * The wait while the AI writes the plan (one call, a few seconds to a few tens of seconds). Nothing is faked: the
 * request folds into a quote, an orb breathes, a bar sweeps and a stopwatch counts.
 */
export function ThinkingBlock({ request, revising }: { request: string | null; revising?: boolean }) {
  return (
    <div className="flex flex-col gap-4" data-testid="ai-thinking">
      {request && (
        <motion.blockquote
          layout
          initial={{ opacity: 0, y: 10, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: dur.slow, ease: ease.out }}
          className="max-h-32 self-end overflow-y-auto whitespace-pre-wrap rounded-ctl bg-brand-soft px-3 py-2 text-[13px] leading-5 text-foreground"
          data-testid="ai-thinking-request"
        >{request}</motion.blockquote>
      )}
      <div className="flex items-center gap-2.5">
        <motion.span
          aria-hidden
          className="size-3.5 shrink-0 rounded-full bg-primary"
          animate={{ scale: [...breathe.animate.scale], opacity: [...breathe.animate.opacity] }}
          transition={{ ...breathe.transition, ease: 'easeInOut' }}
        />
        <SweepText>{revising ? 'Changing it and testing again…' : 'Reading your request…'}</SweepText>
        <span className="flex items-end gap-0.5" aria-hidden>
          {DOTS.map((i) => (
            <motion.span
              key={i}
              className="size-1 rounded-full bg-muted-foreground"
              animate={{ y: [0, -4, 0] }}
              transition={{ duration: 0.7, ease: ease.io, repeat: Infinity, repeatDelay: 0.5, delay: i * 0.12 }}
            />
          ))}
        </span>
        <Elapsed className="ml-auto font-mono text-[12px] tabular-nums text-muted-foreground" data-testid="ai-thinking-timer" />
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-recess" aria-hidden>
        <motion.div
          className="h-full w-1/3 rounded-full bg-primary"
          initial={{ x: '-100%' }}
          animate={{ x: '300%' }}
          transition={{ duration: 1.6, ease: ease.io, repeat: Infinity }}
        />
      </div>
    </div>
  );
}
