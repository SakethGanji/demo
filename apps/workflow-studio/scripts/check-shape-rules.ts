/**
 * Shape-rule conformance bench.
 *
 * `design-prototypes/terminal-shapes.html` runs the cockpit against seven
 * pathological dataset shapes and finds 24 baked-in assumptions, 9 of which
 * break outright. This is the executable half of that bench: it asserts the
 * pure functions in `instrument/shape.ts`, `coverage.ts` and `series.ts`
 * against those same shapes.
 *
 *     npm run check:rules
 *
 * It is a plain script rather than a test-framework suite because the repo has
 * no unit runner and adding one is a bigger decision than this needs. esbuild
 * is already a Vite dependency, so this costs nothing.
 *
 * Shapes referenced below: S1 wide all-numeric (241 cols) · S2 all-unique ·
 * S3a one column · S3b zero rows · S4 hostile names · S5 500-distinct
 * categorical · S6 dirty/mixed types.
 */

import {
  isWideTable, isNarrowTable, detectIdentity, isIdentityLike, dominantType,
  groupByPrefix, addressColumns, middleTruncate, chartCandidates, chartOffers,
  isRatioShaped, typeConformance,
} from '../src/shared/components/instrument/shape'
import { coverage, ratio, isPartial, isEmpty, coverageNote } from '../src/shared/components/instrument/coverage'
import { foldTopN, vizSlot } from '../src/shared/components/instrument/series'

let fail = 0
const eq = (name: string, got: unknown, want: unknown) => {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g !== w) { console.log(`FAIL ${name}\n  got  ${g}\n  want ${w}`); fail++ }
  else console.log(`ok   ${name}`)
}

// ── S6 dirty/mixed types: the sum() silent-wrong-answer ──
const mixed = coverage(968_725, 1_204_880)
eq('R11 partial coverage detected', isPartial(mixed), true)
eq('R11 note states the denominator', coverageNote(mixed), 'over 80.4% · 968,725 rows')
eq('R11 complete coverage has no note', coverageNote(coverage(10, 10)), null)

// ── S3b zero rows: NaN% was the reference bug ──
const none = coverage(0, 0)
eq('R9 zero population -> null ratio, never NaN', ratio(none), null)
eq('R9 zero population is empty', isEmpty(none), true)
eq('R9 zero population emits no note', coverageNote(none), null)

// ── S1 wide all-numeric ──
eq('R1 241 cols is wide', isWideTable(241), true)
eq('R1 9 cols is not wide', isWideTable(9), false)
eq('R8 1 col is narrow', isNarrowTable(1), true)
eq('R8 0 cols is not "narrow" (it is empty)', isNarrowTable(0), false)

const wide = Array.from({ length: 200 }, (_, i) => ({ name: `dv01_${i}m`, dtype: 'float64' }))
  .concat([{ name: 'instrument_id', dtype: 'string' }])
const dom = dominantType(wide)
eq('R3 float64 dominates', dom?.dtype, 'float64')
eq('R3 one exception keeps its chip', dom?.exceptions.length, 1)
eq('R3 no dominance on a tidy mix', dominantType([
  { name: 'a', dtype: 'int' }, { name: 'b', dtype: 'string' }, { name: 'c', dtype: 'date' },
]), null)

// ── R2 identity: highest-distinctness NON-float ──
const cols = [
  { name: 'price', dtype: 'float64', uniqueCount: 1000, nonNullCount: 1000 },
  { name: 'instrument_id', dtype: 'string', uniqueCount: 990, nonNullCount: 1000 },
  { name: 'book', dtype: 'string', uniqueCount: 12, nonNullCount: 1000 },
]
eq('R2 picks non-float despite lower distinctness', detectIdentity(cols)?.column.name, 'instrument_id')
eq('R2 says so when the pick was measured', detectIdentity(cols)?.basis, 'measured')
// Profiling is not automatic on upload, so the no-profile path is the COMMON
// one on a wide file — R2 must still fire, and must admit what it is.
eq('R2 falls back to column 1 without a profile',
   detectIdentity([{ name: 'id', dtype: 'string' }, { name: 'v', dtype: 'float64' }])?.column.name, 'id')
eq('R2 flags the fallback as positional',
   detectIdentity([{ name: 'id', dtype: 'string' }])?.basis, 'positional')
eq('R2 skips floats even in the fallback',
   detectIdentity([{ name: 'px', dtype: 'float64' }, { name: 'sym', dtype: 'string' }])?.column.name, 'sym')
eq('R2 on no columns is still null', detectIdentity([]), null)

// ── S2 all-unique: R4 ──
eq('R4 100% distinct is identity-like', isIdentityLike({ name: 'h', uniqueCount: 91344, nonNullCount: 91344 }), true)
eq('R4 12-of-1000 is not', isIdentityLike({ name: 'b', uniqueCount: 12, nonNullCount: 1000 }), false)

// ── R1 prefix folding ──
const groups = groupByPrefix([
  { name: 'dv01_1m' }, { name: 'dv01_3m' }, { name: 'dv01_6m' },
  { name: 'cs01_1m' }, { name: 'cs01_3m' }, { name: 'cs01_6m' },
  { name: 'id' },
])
eq('R1 folds two families + ungrouped', groups.map(g => `${g.prefix}:${g.columns.length}`), ['dv01_:3','cs01_:3',':1'])
eq('R1 does not invent a family from 2', groupByPrefix([{name:'ab_x'},{name:'ab_y'},{name:'q'}]).map(g=>g.prefix), [''])

// ── S4 hostile names: R10 ──
const addressed = addressColumns(['Amount', 'Total', 'Amount'])
eq('R10 duplicates get ordinals', addressed.map(a => a.label), ['Amount ⟨1⟩', 'Total', 'Amount ⟨3⟩'])
eq('R10 unique names stay clean', addressColumns(['a','b']).map(a => a.label), ['a','b'])
eq('R10 middle-truncate keeps head AND tail', middleTruncate('RT-40118822XXXXXXXXXXNY01', 16), 'RT-40118…XXXNY01')
eq('R10 short strings untouched', middleTruncate('short', 16), 'short')

// ── S1 analytics: R5/R6 ──
const allNumeric = Array.from({ length: 240 }, (_, i) => ({ name: `m${i}`, dtype: 'float64', uniqueCount: 900000, nonNullCount: 1000000 }))
const cand = chartCandidates(allNumeric)
eq('R5 zero dimensions on all-numeric', cand.dimensions.length, 0)
eq('R5 240 measures', cand.measures.length, 240)
const offers = chartOffers(cand)
eq('R5 bar withdrawn with a reason', offers.find(o => o.kind === 'bar')?.available, false)
eq('R5 the reason names the cause', offers.find(o => o.kind === 'bar')?.reason?.includes('dimension'), true)
eq('R6 correlation offered instead', offers.find(o => o.kind === 'correlation')?.available, true)
eq('R5 line withdrawn: no temporal', offers.find(o => o.kind === 'line')?.reason, 'no date or timestamp column exists')

// ── S5 high-cardinality: R7 ──
const zipf = Array.from({ length: 500 }, (_, i) => ({ k: `lei${i}`, v: Math.round(10000 / (i + 1)) }))
const folded = foldTopN(zipf, x => x.v)
eq('R7 head is exactly 8', folded.head.length, 8)
eq('R7 tail is folded, not dropped', folded.other?.count, 492)
eq('R7 tail share is stated', folded.other != null && folded.other.share > 0 && folded.other.share < 1, true)
eq('R7 slot 9 is graphite, never a cycled hue', vizSlot(8), 'var(--m5)')
eq('R7 slot 1 is viz-1', vizSlot(0), 'var(--viz-1)')
eq('R7 two distinct is ratio-shaped', isRatioShaped(2), true)
eq('R7 twelve distinct is not', isRatioShaped(12), false)
eq('R7 empty input folds to nothing', foldTopN([], (x: {v:number}) => x.v).other, null)

// ── R11 badge ──
eq('R11 conformance below 1 is reported', typeConformance({ name: 'q', nonNullCount: 804 }, 1000), 0.804)
eq('R11 clean column needs no qualifier', typeConformance({ name: 'q', nonNullCount: 1000 }, 1000), null)

console.log(fail === 0 ? `\nALL PASS` : `\n${fail} FAILURES`)
process.exit(fail === 0 ? 0 : 1)
