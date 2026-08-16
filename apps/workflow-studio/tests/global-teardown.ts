import { sweepTestDatasets } from './sweep'

/**
 * Remove this run's fixtures, once, after every worker has finished.
 *
 * Deliberately at the end rather than per test: mid-run deletion is visible to
 * other workers' browsers and made tests fail on each other's cleanup.
 */
export default async function globalTeardown() {
  const swept = await sweepTestDatasets()
  if (swept > 0) console.log(`[teardown] removed ${swept} test dataset(s)`)
}
