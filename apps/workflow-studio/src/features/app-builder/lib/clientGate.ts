/**
 * A turn that adds routes regenerates api/client.ts. Screens written in the same turn call the new client methods, so
 * applying them before the client refetch lands compiles them against the old client ("api.x is not a function") and
 * sends a needless auto-fix. The gate queues file updates behind the latest refetch; a failed refetch still releases them.
 */
export function clientGate(refetch: () => Promise<unknown>) {
  let fresh: Promise<unknown> = Promise.resolve()
  return {
    refresh() {
      fresh = refetch().catch(() => undefined)
    },
    after(apply: () => void): Promise<void> {
      const next = fresh.then(apply)
      fresh = next.catch(() => undefined)
      return next
    },
  }
}
