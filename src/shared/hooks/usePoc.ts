import { useEffect, useState } from 'react'
import { teams } from '@/shared/api/catalog'

/** ENSEMBLE_POC_MODE=1 on the API (GET /api/me → poc_mode): single-person testing. Four-eyes greying is skipped in the
 *  UI because the API allows one person to request and decide. Asked once per page load. */
let cached: Promise<boolean> | null = null
export function pocMode(): Promise<boolean> {
  if (!cached) cached = teams.me().then((m) => !!m.poc_mode).catch(() => { cached = null; return false })
  return cached as Promise<boolean>
}
export function usePoc(): boolean {
  const [on, setOn] = useState(false)
  useEffect(() => { let live = true; pocMode().then((v) => live && setOn(v)); return () => { live = false } }, [])
  return on
}
