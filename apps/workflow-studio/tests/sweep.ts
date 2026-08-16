import { api, apiOk, TEST_PREFIX } from './fixtures'

/**
 * Delete every dataset this suite has ever created, identified by name prefix.
 *
 * This replaces per-test cleanup, and the reason is worth recording. Deleting a
 * fixture the moment its test ended meant one worker was destroying datasets
 * while another worker's browser had the catalog open — the second worker then
 * logged a 404 for a row that had just vanished, and failed on an error that
 * had nothing to do with what it was testing. Tests must not be able to break
 * each other, and cleanup is not exempt from that.
 *
 * Sweeping by prefix is also strictly better at the job the old per-test
 * cleanup was meant to do: it runs at both ends, so a run that crashes halfway
 * is tidied by the *next* run rather than leaking forever. (The suite this
 * replaces leaked on every crash, and the database drifted to 74 datasets.)
 */
export async function sweepTestDatasets(): Promise<number> {
  let deleted = 0

  // Page through rather than assuming one request sees everything.
  for (let guard = 0; guard < 20; guard++) {
    const page = await apiOk<{ items: { id: string; name: string }[] }>(
      'GET',
      `/datasets?q=${encodeURIComponent(TEST_PREFIX)}&limit=200`,
    )
    const mine = page.items.filter((d) => d.name.startsWith(TEST_PREFIX))
    if (mine.length === 0) break

    for (const d of mine) {
      const res = await api('DELETE', `/datasets/${d.id}`)
      // 404 is fine — a concurrent sweep may have taken it already.
      if (res.status < 300 || res.status === 404) deleted++
    }
  }

  return deleted
}
