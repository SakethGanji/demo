import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { appsApi, type ApiAppDetail, type ApiAppFile } from '@/shared/lib/api'
import type { AppSummary } from './useApps'

// Bumped any time the shape we write to disk changes. Imports check this.
const APP_EXPORT_FORMAT_VERSION = 1

/** Portable subset of an app — what we write to disk on export and accept on import.
 *
 * Excludes per-DB fields (id, timestamps, slug, access settings, active flag,
 * version metadata). Including those would only cause conflicts on import to a
 * different DB. Slug is unique across the team; access settings should be set
 * fresh on import; version history is intentionally not preserved (only the
 * latest source_code + files snapshot is restored, as a fresh v1).
 */
export interface AppExportPayload {
  format: 'workflow-studio.app'
  version: number
  exportedAt: string
  app: {
    name: string
    description?: string | null
    definition: Record<string, unknown>
    workflow_ids: string[]
    api_execution_ids: string[]
    source_code: string | null
    files: ApiAppFile[]
  }
}

function downloadJson(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function slugifyForFilename(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'app'
  )
}

/** Returns the JSON payload that gets written to disk. Pure data transform —
 * separated so a future "copy to clipboard" action can reuse it.
 */
export function buildAppExportPayload(detail: ApiAppDetail): AppExportPayload {
  return {
    format: 'workflow-studio.app',
    version: APP_EXPORT_FORMAT_VERSION,
    exportedAt: new Date().toISOString(),
    app: {
      name: detail.name,
      description: (detail as unknown as { description?: string | null }).description ?? null,
      definition: detail.definition,
      workflow_ids: detail.workflow_ids ?? [],
      api_execution_ids: detail.api_execution_ids ?? [],
      source_code: detail.source_code ?? null,
      files: detail.files ?? [],
    },
  }
}

function isAppExportPayload(value: unknown): value is AppExportPayload {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  if (v.format !== 'workflow-studio.app') return false
  const app = v.app as Record<string, unknown> | undefined
  if (!app || typeof app.name !== 'string' || typeof app.definition !== 'object') return false
  return true
}

/** Default blank definition for a new app. */
const BLANK_DEFINITION = {
  sourceCode: null,
}

export function useCreateApp() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [isCreating, setIsCreating] = useState(false)

  const handleCreate = async () => {
    if (isCreating) return
    setIsCreating(true)
    try {
      const result = await appsApi.create({
        name: 'Untitled App',
        definition: BLANK_DEFINITION,
      })
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      navigate({ to: '/builder', search: { appId: result.id } })
    } catch (error) {
      toast.error('Failed to create app', {
        description: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setIsCreating(false)
    }
  }

  return { isCreating, handleCreate }
}

export function useAppActions(app: AppSummary) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [isDeleting, setIsDeleting] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const handleOpen = () => {
    navigate({ to: '/builder', search: { appId: app.id } })
  }

  const handleDelete = async () => {
    if (isDeleting) return
    setIsDeleting(true)
    try {
      await appsApi.delete(app.id)
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      setDeleteDialogOpen(false)
      toast.success('App deleted', {
        description: `"${app.name}" has been deleted.`,
      })
    } catch (error) {
      toast.error('Failed to delete app', {
        description: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setIsDeleting(false)
    }
  }

  const handleExport = async () => {
    if (isExporting) return
    setIsExporting(true)
    try {
      const detail = await appsApi.get(app.id)
      const payload = buildAppExportPayload(detail)
      const filename = `${slugifyForFilename(detail.name)}.app.json`
      downloadJson(filename, payload)
      toast.success('App exported', { description: filename })
    } catch (error) {
      toast.error('Failed to export app', {
        description: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setIsExporting(false)
    }
  }

  return {
    isDeleting,
    isExporting,
    deleteDialogOpen,
    setDeleteDialogOpen,
    handleOpen,
    handleDelete,
    handleExport,
  }
}

/** Top-level "Import app" hook for the projects page. Reads a JSON file
 * exported via `handleExport`, creates a new app, attaches the source +
 * multi-file payload as v1, then navigates to the builder.
 */
export function useImportApp() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [isImporting, setIsImporting] = useState(false)

  const importFromJson = async (json: string): Promise<boolean> => {
    if (isImporting) return false
    let parsed: unknown
    try {
      parsed = JSON.parse(json)
    } catch (error) {
      toast.error('Invalid app file', {
        description: error instanceof Error ? error.message : 'Could not parse JSON',
      })
      return false
    }

    if (!isAppExportPayload(parsed)) {
      toast.error('Invalid app file', {
        description: 'Missing required fields (format / app.name / app.definition).',
      })
      return false
    }
    if (parsed.version > APP_EXPORT_FORMAT_VERSION) {
      toast.warning('Newer export format', {
        description: `File version is ${parsed.version}; this client only knows version ${APP_EXPORT_FORMAT_VERSION}. Trying anyway.`,
      })
    }

    const { app } = parsed
    setIsImporting(true)
    try {
      // 1. Create the bare app row. POST /apps only takes name + definition
      //    in the current TS signature; everything else is set on the
      //    follow-up PUT (avoids extending the create signature and keeps
      //    backend behavior identical to manual creation).
      const created = await appsApi.create({ name: app.name, definition: app.definition })

      // 2. Push source_code + files + linked-resource IDs + description.
      //    create_version=true so the imported state becomes v1 with a
      //    trigger label of 'import' (auditable in version history).
      await appsApi.update(created.id, {
        description: app.description ?? undefined,
        workflow_ids: app.workflow_ids,
        api_execution_ids: app.api_execution_ids,
        source_code: app.source_code ?? '',
        files: app.files,
        create_version: true,
        version_trigger: 'import',
      })

      queryClient.invalidateQueries({ queryKey: ['apps'] })
      toast.success('App imported', { description: `"${created.name}" created.` })
      navigate({ to: '/builder', search: { appId: created.id } })
      return true
    } catch (error) {
      toast.error('Failed to import app', {
        description: error instanceof Error ? error.message : 'Unknown error',
      })
      return false
    } finally {
      setIsImporting(false)
    }
  }

  /** Trigger an <input type="file"> dialog and import the selected file. */
  const promptForFileAndImport = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'application/json,.json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      const text = await file.text()
      await importFromJson(text)
    }
    input.click()
  }

  return { isImporting, importFromJson, promptForFileAndImport }
}
