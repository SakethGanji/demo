/**
 * Save ⌘S (UI-DESIGN §4.2 step 6):
 *  1. create checked draft tables (`409 name_taken` = exists → continue)
 *  2. saved module: `PUT …?dry_run=true {source, base_sha}` → breaking changes (incl. auth
 *     loosening) open BreakingChangesDialog
 *  3. `POST …/modules {name, source}` or `PUT …/modules/{M} {source, base_sha}`
 *  4. `409 module_changed` → ModuleChangedDialog (Overwrite re-sends without base_sha)
 *  5. toast "Saved refunds.py · 3 routes created · table refunds created" + invalidations
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { modulesApi, tablesApi } from '@/shared/api/endpoints'
import { ApiError, errorMessage, isApiError } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { Change, ModuleWriteResult } from '@/shared/api/types'
import { useDocStore, clearPersisted, type ModuleDoc } from './docStore'
import { problemsFromError, type Problem } from './useAnalysis'

export interface SaveOutcome {
  ok: boolean
  result?: ModuleWriteResult
}

export function useSaveModule(pid: string, existingTables: string[]) {
  const qc = useQueryClient()
  const patch = useDocStore((s) => s.patch)
  const [saving, setSaving] = useState(false)
  const [problems, setProblems] = useState<{ source: string; problems: Problem[] } | null>(null)
  const [breaking, setBreaking] = useState<Change[] | null>(null)
  const [conflict, setConflict] = useState<{ currentSha: string; updatedAt: string | null } | null>(null)
  const [nameTaken, setNameTaken] = useState(false)

  const save = async (doc: ModuleDoc, opts: { confirmedBreaking?: boolean; overwrite?: boolean } = {}): Promise<SaveOutcome> => {
    if (saving) return { ok: false }
    setSaving(true)
    setNameTaken(false)
    const source = doc.source
    const createdTables: string[] = []
    try {
      // 1. declared tables
      for (const t of doc.draft?.tables ?? []) {
        if (!t.create || existingTables.includes(t.name)) continue
        try {
          await tablesApi.create(pid, { name: t.name, columns: t.columns })
          createdTables.push(t.name)
        } catch (e) {
          if (!isApiError(e, 'name_taken')) throw e
        }
      }
      if (createdTables.length) await invalidate.tablesChanged(qc, pid)

      // 2. breaking-change preview for saved modules
      const baseSha = opts.overwrite ? null : doc.baseSha
      if (doc.moduleId && !opts.confirmedBreaking) {
        const dry = await modulesApi.updateDryRun(pid, doc.moduleId, { source, base_sha: baseSha ?? undefined })
        if (dry.breaking_changes.length > 0) {
          setBreaking(dry.breaking_changes)
          return { ok: false }
        }
      }

      // 3. write
      const result = doc.moduleId
        ? await modulesApi.update(pid, doc.moduleId, { source, base_sha: baseSha ?? undefined })
        : await modulesApi.create(pid, { name: doc.module, source })

      patch(doc.projectId, doc.module, {
        moduleId: result.module.id,
        savedSource: source,
        baseSha: result.module.source_sha,
        isNew: false,
        draft: null,
        staleRestore: false,
      })
      clearPersisted(doc.projectId, doc.module)
      qc.setQueryData(qk.module(pid, result.module.id), { ...result.module, source })
      setProblems(null)
      setBreaking(null)
      setConflict(null)
      await invalidate.modulesChanged(qc, pid)
      const created = result.routes.created.length
      const removed = result.routes.removed.length
      const parts = [`Saved ${doc.module}.py`]
      if (created) parts.push(`${created} route${created === 1 ? '' : 's'} created`)
      if (removed) parts.push(`${removed} removed`)
      for (const t of createdTables) parts.push(`table ${t} created`)
      toast.success(parts.join(' · '))
      return { ok: true, result }
    } catch (e) {
      const ps = problemsFromError(e)
      if (e instanceof ApiError && e.code === 'module_changed') {
        const d = e.details as { current_sha?: string; updated_at?: string }
        setConflict({ currentSha: d.current_sha ?? '', updatedAt: d.updated_at ?? null })
      } else if (e instanceof ApiError && e.code === 'name_taken') {
        setNameTaken(true)
        toast.error(`Not saved: a module called "${doc.module}" already exists`)
      } else if (ps && ps.length) {
        setProblems({ source, problems: ps })
        const first = ps[0]
        const what = first.code === 'syntax_error' ? 'syntax error' : first.code === 'route_conflict' ? 'route conflict' : 'invalid decorator'
        toast.error(`Not saved: ${what}${first.line ? ` on line ${first.line}` : ''}`)
      } else {
        toast.error('Not saved', { description: errorMessage(e) })
      }
      return { ok: false }
    } finally {
      setSaving(false)
    }
  }

  return {
    save,
    saving,
    problems,
    clearProblems: () => setProblems(null),
    breaking,
    clearBreaking: () => setBreaking(null),
    conflict,
    clearConflict: () => setConflict(null),
    nameTaken,
  }
}
