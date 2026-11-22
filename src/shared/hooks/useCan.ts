import { useEffect, useState } from 'react'
import { authz, type AuthzDecision } from '@/shared/api/governance'

/** P19: ask the API whether the acting person may do `action` on the object; a button greys out on `allow: false`,
 *  with the reason as its title. Audit mode lets a would-deny request through, so this is the honest signal.
 *  (U3: moved here from models/data/Governance so the F-PII pages and Settings share it.) */
export function useCan(action: string, kind: string | null, id: string | null, by: string): AuthzDecision | null {
  const [d, setD] = useState<AuthzDecision | null>(null)
  useEffect(() => {
    let live = true
    if (!by) { setD(null); return }
    setD(null)
    authz.check(action, kind, id).then((r) => live && setD(r)).catch(() => live && setD(null))
    return () => { live = false }
  }, [action, kind, id, by])
  return d
}
export const why = (d: AuthzDecision | null, fallback?: string) => (d && !d.allow ? `Not allowed for you: ${d.reason ?? `needs ${d.needed}`}${d.role && !(d.reason ?? '').includes('you are') ? ` (you are ${d.role})` : ''}.` : fallback)
