/**
 * `project.settings.onboarding` (how the project started, deferred / dismissed steps, the first success), saved
 * through the project update (`PUT /projects/{p}` merges `settings`).
 *
 * The cached project is patched first, so the outline and the Overview (each running `useSetup`) see a change
 * at once. If the save fails, the value is kept in a per-project localStorage mirror and retried on the next load;
 * the guide keeps working either way. The old `luna:setup-active` / `luna:setup-skip` flags migrate on first read.
 */
import { useCallback, useEffect } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { Project } from '@/shared/api/types'
import { useProjectCtx } from '../ProjectContext'
import { EMPTY_ONBOARDING, normalizeOnboarding, type Onboarding } from '../lib/nextSteps'

const mirrorKey = (pid: string) => `luna:onboarding:${pid}`
const legacyActive = (pid: string) => `luna:setup-active:${pid}`
const legacySkip = (pid: string) => `luna:setup-skip:${pid}`

function lsGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function lsSet(key: string, value: string | null) {
  try {
    if (value == null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* in-memory only */
  }
}

function readMirror(pid: string): Onboarding | null {
  const raw = lsGet(mirrorKey(pid))
  if (!raw) return null
  try {
    return normalizeOnboarding(JSON.parse(raw))
  } catch {
    return null
  }
}

/** The pre-onboarding guide's flags: an active guide was the backend-first one. */
function readLegacy(pid: string): Onboarding | null {
  const active = lsGet(legacyActive(pid)) === '1'
  const skipped = lsGet(legacySkip(pid)) === '1'
  if (!active && !skipped) return null
  return { ...EMPTY_ONBOARDING, start: active ? 'backend' : null }
}

// one migration / retry per project per page load (the outline and the Overview both run the hook)
const synced = new Set<string>()
// the newest save per project, so an older response never overwrites a newer value
const seq = new Map<string, number>()

function patchCache(qc: QueryClient, keys: string[], next: Onboarding) {
  for (const k of keys)
    qc.setQueryData<Project>(qk.projectRef(k), (old) => (old ? { ...old, settings: { ...old.settings, onboarding: next } } : old))
}

async function persist(qc: QueryClient, pid: string, slug: string, next: Onboarding) {
  const n = (seq.get(pid) ?? 0) + 1
  seq.set(pid, n)
  patchCache(qc, [slug, pid], next)
  lsSet(mirrorKey(pid), JSON.stringify(next))
  try {
    const p = await projectsApi.update(pid, { settings: { onboarding: next } })
    if (seq.get(pid) !== n) return
    lsSet(mirrorKey(pid), null)
    qc.setQueryData(qk.projectRef(p.slug), p)
    qc.setQueryData(qk.projectRef(p.id), p)
  } catch {
    /* the mirror keeps it; retried on the next load */
  }
}

/** Saves onboarding for a project outside its page (the home start flows), best effort. */
export async function saveOnboarding(pid: string, next: Onboarding): Promise<void> {
  try {
    await projectsApi.update(pid, { settings: { onboarding: next } })
  } catch {
    lsSet(mirrorKey(pid), JSON.stringify(next))
  }
}

export interface OnboardingStore {
  /** null: nothing stored (the project was not started through the picker) */
  value: Onboarding | null
  save: (next: Onboarding) => void
  /** merge a change into the current value (or the empty one) and save */
  update: (fn: (cur: Onboarding) => Onboarding) => void
}

export function useOnboarding(): OnboardingStore {
  const { project, pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const server = normalizeOnboarding(project.settings?.onboarding)
  const mirror = readMirror(pid)
  const legacy = server || mirror ? null : readLegacy(pid)
  const value = mirror ?? server ?? legacy

  const save = useCallback((next: Onboarding) => void persist(qc, pid, slug, next), [qc, pid, slug])
  const update = useCallback((fn: (cur: Onboarding) => Onboarding) => save(fn(value ?? EMPTY_ONBOARDING)), [save, value])

  // migrate the old flags, or retry a save that failed, once
  useEffect(() => {
    if (synced.has(pid)) return
    synced.add(pid)
    const pending = readMirror(pid)
    if (pending) {
      void persist(qc, pid, slug, pending)
    } else if (!normalizeOnboarding(project.settings?.onboarding)) {
      const old = readLegacy(pid)
      if (old) void persist(qc, pid, slug, old)
    }
    lsSet(legacyActive(pid), null)
    lsSet(legacySkip(pid), null)
  }, [qc, pid, slug, project.settings])

  return { value, save, update }
}
