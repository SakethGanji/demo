/** A circled check whose ring and tick draw in (a static check when the user asked for reduced motion). */
import { motion } from 'motion/react'
import { cn } from '@/shared/lib/utils'
import { useCalm } from './motion'
export function DrawCheck({ className, delay = 0 }: { className?: string; delay?: number }) {
  const calm = useCalm()
  const draw = (d: number) => (calm ? { initial: false as const } : { initial: { pathLength: 0, opacity: 0 }, animate: { pathLength: 1, opacity: 1 }, transition: { duration: 0.32, delay: delay + d, ease: [0.65, 0, 0.35, 1] as const } })
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={cn('size-3.5', className)} aria-hidden>
      <motion.circle cx="12" cy="12" r="10" {...draw(0)} />
      <motion.path d="m9 12 2 2 4-4" {...draw(0.18)} />
    </svg>
  )
}
