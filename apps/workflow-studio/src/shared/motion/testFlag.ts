/**
 * The one motion-off switch (PLAN-MOTION-UI §3.5). E2E sets `window.__STUDIO_TEST_MOTION__ = 'off'` before the app
 * loads; playback then applies every round in one tick and `motion` skips its animations. It is not a user setting.
 */
import { MotionGlobalConfig } from 'motion/react'

declare global {
  var __STUDIO_TEST_MOTION__: string | undefined
}

let cached: boolean | null = null

/** True when tests asked for instant playback. Read once per page. */
export function motionOff(): boolean {
  if (cached === null) {
    cached = globalThis.__STUDIO_TEST_MOTION__ === 'off'
    if (cached) MotionGlobalConfig.skipAnimations = true
  }
  return cached
}

/** For unit tests only: forget the cached read. */
export function resetMotionFlagForTests(): void {
  cached = null
  MotionGlobalConfig.skipAnimations = false
}
