import { afterEach, describe, expect, it } from 'vitest'
import { MotionGlobalConfig } from 'motion/react'
import { motionOff, resetMotionFlagForTests } from './testFlag'

afterEach(() => {
  delete globalThis.__STUDIO_TEST_MOTION__
  resetMotionFlagForTests()
})

describe('motionOff', () => {
  it('is false without the flag and leaves motion alone', () => {
    expect(motionOff()).toBe(false)
    expect(MotionGlobalConfig.skipAnimations).toBeFalsy()
  })
  it("is true for 'off' and tells motion to skip animations", () => {
    globalThis.__STUDIO_TEST_MOTION__ = 'off'
    expect(motionOff()).toBe(true)
    expect(MotionGlobalConfig.skipAnimations).toBe(true)
  })
  it('reads the flag once per page', () => {
    expect(motionOff()).toBe(false)
    globalThis.__STUDIO_TEST_MOTION__ = 'off'
    expect(motionOff()).toBe(false)
  })
  it('only the exact value counts', () => {
    globalThis.__STUDIO_TEST_MOTION__ = 'true'
    expect(motionOff()).toBe(false)
  })
})
