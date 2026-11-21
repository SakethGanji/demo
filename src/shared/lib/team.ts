import { useEffect, useState } from 'react'
import { teams, type Me, type MyTeam } from '@/shared/api/catalog'

/** U1: who is acting (typed, no sign-in) and the current team (the sidebar switcher). Both live in localStorage, and a
 *  change is broadcast so the switcher, Build › Data and the upload flow follow it at once. */
const ACTOR = 'ensemble.actor', TEAM = 'ensemble.team', EV = 'ensemble:who'
const get = (k: string) => { try { return localStorage.getItem(k) || '' } catch { return '' } }
const set = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* private mode */ } dispatchEvent(new Event(EV)) }

export const readActor = () => get(ACTOR)
export const saveActor = (s: string) => set(ACTOR, s)
export const saveTeam = (id: string) => set(TEAM, id)

let cache: { actor: string; p: Promise<Me> } | null = null
const me = (actor: string) => { if (!cache || cache.actor !== actor) cache = { actor, p: teams.me() }; return cache.p }

/** The acting name, the teams they are in (GET /api/me) and the current one: the stored choice if they are still in it,
 *  else their only team (`default_team`), else the first. */
export function useTeam(): { actor: string; teams: MyTeam[]; team: MyTeam | null; err: string } {
  const [actor, setActor] = useState(readActor)
  const [stored, setStored] = useState(() => get(TEAM))
  const [m, setM] = useState<Me | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => { const f = () => { setActor(readActor()); setStored(get(TEAM)) }; addEventListener(EV, f); return () => removeEventListener(EV, f) }, [])
  useEffect(() => {
    let live = true
    setErr('')
    if (!actor) { setM(null); return }
    me(actor).then((r) => live && setM(r)).catch((e) => { cache = null; if (live) setErr((e as Error).message) })
    return () => { live = false }
  }, [actor])
  const list = m?.teams.filter((t) => t.state === 'active') ?? []
  const team = list.find((t) => t.id === stored) ?? list.find((t) => t.id === m?.default_team) ?? list[0] ?? null
  return { actor, teams: list, team, err }
}
