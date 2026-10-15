/**
 * Toasts raised before the root layout (and its <Toaster/>) has mounted — e.g. from a router `beforeLoad`
 * redirect on a cold page load — are dropped by sonner. Queue them here; the root layout flushes after mount.
 */
import { toast } from 'sonner'

type Kind = 'error' | 'success' | 'info'
const pending: Array<{ kind: Kind; message: string }> = []
let mounted = false

export function queueToast(kind: Kind, message: string): void {
  if (mounted) toast[kind](message)
  else pending.push({ kind, message })
}

export function flushPendingToasts(): void {
  mounted = true
  while (pending.length) {
    const t = pending.shift()!
    toast[t.kind](t.message)
  }
}
