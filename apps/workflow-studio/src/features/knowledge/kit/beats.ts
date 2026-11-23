/** Knowledge-only motion constants (seconds). The shared `tokens.ts` stays untouched (PLAN-CAPGRAPH-UI §1.5). */
export const kg = {
  assemble: {
    maxTotal: 3.2, conceptStagger: 0.04, roleStagger: 0.03, capWindow: 1.0, capStaggerMax: 0.03,
    topAt: 1.4, edgesAt: 1.5, edgeDraw: 0.26, maxComets: 12, compactAbove: 160,
  },
  ego: {
    exitDur: 0.2, flipStagger: 0.025, ringAt: 0.46, ringDraw: 0.4, cometAt: 0.48, cometGap: 0.04, cometDraw: 0.26,
    labelChar: 0.018, labelCap: 0.2, sheetAt: 0.2, maxFlip: 80, maxLabels: 16,
  },
  memory: { landGap: 0.4, typeChar: 0.018, typeCap: 0.36, railDraw: 0.2 },
  chips: { debounceMs: 400, minChars: 12, max: 8 },
} as const
