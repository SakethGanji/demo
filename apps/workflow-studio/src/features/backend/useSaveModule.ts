/**
 * Save ⌘S (UI-DESIGN §4.2 step 6):
 *  1. checked draft tables go with the save as `tables`: the engine creates them, or migrates existing ones additively,
 *     before it writes the module (one server path; a destructive change refuses the whole save)
 *  2. saved module: `PUT …?dry_run=true {source, base_sha}` → breaking changes (incl. auth
 *     loosening) open BreakingChangesDialog
 *  3. `POST …/modules {name, source}` or `PUT …/modules/{M} {source, base_sha}`
 *  4. `409 module_changed` → ModuleChangedDialog (Overwrite re-sends without base_sha)
 *  5. toast "Saved refunds.py · 3 routes created · table refunds created" + invalidations
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { modulesApi } from '@/shared/api/endpoints'
import { ApiError, errorMessage } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { Change, DeclaredTable, ModuleWriteResult } from '@/shared/api/types'
import { useDocStore, clearPersisted, type ModuleDoc } from './docStore'
import { problemsFromError, type Problem } from './useAnalysis'

/** The draft's checked tables as the module save takes them (`tables` on create / update). */
export function saveTables(doc: Pick<ModuleDoc, 'draft'>): DeclaredTable[] {
  return (doc.draft?.tables ?? []).filter((t) => t.create).map((t) => ({ name: t.name, columns: t.columns }))
}

/** The body of the module write: the source, the base sha of a saved module, and the declared tables. */
export function saveBody(doc: Pick<ModuleDoc, 'draft' | 'module' | 'moduleId'>, source: string, baseSha: string | null) {
  const tables = saveTables(doc)
  const extra = tables.length ? { tables } : {}
  return doc.moduleId ? { source, base_sha: baseSha ?? undefined, ...extra } : { name: doc.module, source, ...extra }
}

/** Tables the save created: the engine's `tables_created` when it reports it, else the declared ones that did not exist. */
export function createdTables(result: ModuleWriteResult, sent: DeclaredTable[], existing: string[]): string[] {
  const reported = result.tables_created
  if (Array.isArray(reported)) return reported.map((t) => (typeof t === 'string' ? t : String(t.name ?? '')))
  return sent.map((t) => t.name).filter((n) => !existing.includes(n))
}

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
    try {
      // 1. declared tables travel with the write (step 3)
      const tables = saveTables(doc)

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
        ? await modulesApi.update(pid, doc.moduleId, saveBody(doc, source, baseSha))
        : await modulesApi.create(pid, saveBody(doc, source, baseSha) as { name: string; source: string; tables?: DeclaredTable[] })
      const made = createdTables(result, tables, existingTables)
      if (tables.length) await invalidate.tablesChanged(qc, pid)

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
      for (const t of made) parts.push(`table ${t} created`)
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
