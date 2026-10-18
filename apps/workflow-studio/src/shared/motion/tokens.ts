/**
 * JS motion tokens for the AI build / review choreography (PLAN-MOTION-UI §3.1). They mirror the CSS tokens in
 * index.css and add the AI-only ones. Durations are in seconds (the unit `motion` uses).
 *
 * Exception recorded in DESIGN-STANDARD.md: while an AI build or review plays, these may use a small spring
 * overshoot and the AI marker may pulse; nothing else loops.
 */

export const dur = {
  instant: 0.08,
  fast: 0.14,
  base: 0.2,
  slow: 0.28,
  /** node assembly in total: ring .18 and icon .16 overlap, label typing <= .25 */
  assemble: 0.44,
  /** edge draw-in */
  draw: 0.32,
  /** the spark dot running an edge */
  spark: 0.4,
  /** one camera move */
  camera: 0.7,
  /** tween between rounds */
  move: 0.45,
  /** settle on Keep */
  settle: 0.6,
  /** one test spark hop along a path */
  hop: 0.12,
  /** shockwave ring on a failing node */
  shockwave: 0.5,
  /** one-shot shake of a failing tile */
  shake: 0.36,
} as const

export const stagger = {
  /** nominal gap between ops of a round; compressed by the round budget */
  op: 0.09,
  /** sidebar rows */
  list: 0.035,
} as const

export const round = {
  /** hard cap on how long one round takes to play */
  budget: 3.5,
  /** animations in flight at once */
  maxConcurrent: 12,
  /** above this many nodes in a round, assembly drops to icon + fade and sparks are skipped */
  reducedAbove: 60,
  /** above this many moving nodes, moves snap instead of tween */
  maxTweened: 40,
  /** a round that is behind the live build plays this much faster */
  catchUp: 3,
  /** a round with a newer one waiting behind it ends within this many seconds of its start */
  hurry: 1.2,
} as const

export type Bezier = readonly [number, number, number, number]
export const ease = {
  out: [0.16, 1, 0.3, 1] as Bezier,
  io: [0.65, 0, 0.35, 1] as Bezier,
  in: [0.4, 0, 1, 1] as Bezier,
}

export const spring = {
  /** icon pop, test tick (small overshoot) */
  snap: { type: 'spring', stiffness: 520, damping: 30, mass: 0.8 },
  /** AI marker travel */
  soft: { type: 'spring', stiffness: 220, damping: 26 },
  /** settle, no overshoot */
  firm: { type: 'spring', stiffness: 700, damping: 50 },
} as const

/** The AI marker's idle pulse. The only looping animation in the build. */
export const breathe = {
  animate: { scale: [1, 1.12, 1], opacity: [0.7, 1, 0.7] },
  transition: { duration: 1.6, repeat: Infinity, ease: 'easeInOut' },
} as const

/** Seconds an op occupies after it starts (its longest single effect); used to fit a round in its budget. */
export const opDuration = {
  roundStart: 0,
  roundEnd: 0,
  addNode: dur.assemble,
  moveNode: dur.move,
  changeNode: dur.slow + 1.2,
  removeNode: dur.slow,
  addEdge: dur.draw + dur.spark,
  removeEdge: dur.draw,
  fail: dur.shockwave * 2,
  testRun: 0,
} as const

/** One-node-at-a-time build beats (PLAN-MOTION-V2 §2). Seconds, at time scale k = 1. */
export const beat = {
  roundLead: 0.5, lead: 0.12, period: 0.74, exitPeriod: 0.46, morphPeriod: 0.7,
  plate: 0.16, frameAt: 0.06, frame: 0.22, scanAt: 0.22, scan: 0.18, iconAt: 0.32, labelAt: 0.36, label: 0.2,
  portsAt: 0.46, ports: 0.12, lockAt: 0.56, lockHold: 0.24, lockPulse: 0.32,
  edgeAt: 0.58, edgeDraw: 0.26, edgeGap: 0.06, linkGap: 0.08,
  exitIdleAt: 0.36, morphLockAt: 0.52,
  retract: 0.24, tail: 0.3,
  hop: 0.14, minHop: 0.07, testGap: 0.12, testCap: 3.0, failHold: 0.6,
  compactBelow: 0.55, minK: 0.3, minPeriod: 0.22,
  catchUp1: 1.4, catchUp2: 2.2,
  followMs: 650, pullBackMs: 900,
} as const

/** Round cap that scales with the node count: clamp(1.5 + 0.74 n, 3.5, 12) seconds. */
export const roundCap = (n: number) => Math.min(12, Math.max(3.5, 1.5 + beat.period * n))
