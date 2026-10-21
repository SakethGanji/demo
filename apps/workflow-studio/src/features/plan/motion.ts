/**
 * The build's motion vocabulary, shared by the timeline, the live code and preview panes and the finish card: quick
 * springs (no cartoon bounce) and `useCalm()` (the user asked for reduced motion: plain fades only, no movement, no
 * typing). `DrawCheck` (motionKit.tsx) is the check mark whose path draws in.
 */
import { useReducedMotion, type Transition } from 'motion/react'

/** A firm, quick spring for things that move into place. */
export const SPRING: Transition = { type: 'spring', stiffness: 520, damping: 40, mass: 0.8 }
/** A softer one for panes and the composer travelling across the screen. */
export const GLIDE: Transition = { type: 'spring', stiffness: 260, damping: 32, mass: 0.9 }
export const FADE: Transition = { duration: 0.18, ease: [0.16, 1, 0.3, 1] }

/** True when the user asked for reduced motion. */
export function useCalm(): boolean {
  return !!useReducedMotion()
}
