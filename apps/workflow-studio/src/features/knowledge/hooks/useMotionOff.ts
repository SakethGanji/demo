import { useReducedMotion } from 'motion/react'
import { motionOff } from '@/shared/motion/testFlag'

/** True when JS motion should jump to its end: the OS asks for reduced motion, or a test turned motion off. */
export function useMotionOff(): boolean {
  const reduce = useReducedMotion()
  return !!reduce || motionOff()
}
