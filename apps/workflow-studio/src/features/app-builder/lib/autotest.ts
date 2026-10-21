/**
 * The build's scenario checks: `autotest` on the done/result payload, roughly `{passed, total, results: [{name, ok, reason?}]}`.
 * Read tolerantly: a build without the field, or with a malformed one, simply has no checks to show.
 */
export interface AutotestResult {
  name: string
  ok: boolean
  reason?: string
}

export interface Autotest {
  passed: number
  total: number
  results: AutotestResult[]
}

export function autotestOf(raw: unknown): Autotest | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const results: AutotestResult[] = Array.isArray(r.results)
    ? r.results
        .filter((x): x is Record<string, unknown> => !!x && typeof x === 'object')
        .map((x, i) => ({
          name: typeof x.name === 'string' && x.name ? x.name : `Check ${i + 1}`,
          ok: x.ok === true || x.passed === true,
          reason: typeof x.reason === 'string' && x.reason ? x.reason : undefined,
        }))
    : []
  const total = typeof r.total === 'number' ? r.total : results.length
  const passed = typeof r.passed === 'number' ? r.passed : results.filter((x) => x.ok).length
  if (!total && !results.length) return null
  return { passed: Math.min(passed, total), total, results }
}

/** Look for it on the build, its report, or the finishing step: wherever the engine put it. */
export function autotestFromBuild(build: unknown): Autotest | null {
  if (!build || typeof build !== 'object') return null
  const b = build as Record<string, unknown>
  const report = (b.report && typeof b.report === 'object' ? b.report : {}) as Record<string, unknown>
  const result = (b.result && typeof b.result === 'object' ? b.result : {}) as Record<string, unknown>
  return autotestOf(b.autotest) ?? autotestOf(report.autotest) ?? autotestOf(result.autotest)
}

export function failedChecks(a: Autotest): AutotestResult[] {
  return a.results.filter((r) => !r.ok)
}

export function checksLabel(a: Autotest): string {
  return `Checks ${a.passed}/${a.total} passing`
}
