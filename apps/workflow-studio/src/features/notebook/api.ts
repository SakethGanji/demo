/**
 * Notebook API (engine routes/notebooks.py + routes/notebook_keep.py). One function per endpoint, typed by ./types.
 * Routes that are not built yet answer 501 `not_implemented`; callers treat that as "not available yet".
 */
import { apiUrl, isApiError, request, requestBlob, requestText } from '@/shared/api/http'
import type {
  ApplyRequest, ArtifactInfo, AskResponse, AskSelection, CellInput, ImportHttpRequest, ImportHttpResponse, NotebookEnvironments, Capabilities, Cell, CellActionResponse, CellCreate, CellPatch,
  CellPatchResponse, ChecksResponse, ControlAction, DiscardRequest, ForEachPreview, KeepPreview,
  KeepRequest, KeepResponse, Notebook, NotebookCreate, NotebookListItem, NotebookPatch, NotebookTemplate,
  ReportResponse, RetryRowsRequest, RowsPage, RowsQuery, Run, RunCreate, TraceEntry,
} from './types'

const enc = encodeURIComponent

/** True for a route that exists in the contract but is not built yet (501 not_implemented). */
export function isNotBuilt(e: unknown): boolean {
  return isApiError(e, undefined, 501) || isApiError(e, 'not_implemented') || isApiError(e, undefined, 405)
}

export const notebookApi = {
  // notebooks
  list: (project: string, q?: { status?: string; q?: string }) =>
    request<NotebookListItem[]>(`/projects/${enc(project)}/notebooks`, { query: q }),
  create: (project: string, body: NotebookCreate) => request<Notebook>(`/projects/${enc(project)}/notebooks`, { method: 'POST', body }),
  get: (id: string) => request<Notebook>(`/notebooks/${enc(id)}`),
  patch: (id: string, body: NotebookPatch) => request<Notebook>(`/notebooks/${enc(id)}`, { method: 'PATCH', body }),
  remove: (id: string) => request<void>(`/notebooks/${enc(id)}`, { method: 'DELETE' }),

  // cells
  addCell: (id: string, body: CellCreate) => request<Cell>(`/notebooks/${enc(id)}/cells`, { method: 'POST', body }),
  patchCell: (cellId: string, body: CellPatch) => request<CellPatchResponse>(`/notebook-cells/${enc(cellId)}`, { method: 'PATCH', body }),
  deleteCell: (cellId: string) => request<void>(`/notebook-cells/${enc(cellId)}`, { method: 'DELETE' }),
  rows: (cellId: string, q: RowsQuery = {}) => request<RowsPage>(`/notebook-cells/${enc(cellId)}/rows`, { query: { ...q } }),
  preview: (cellId: string, n = 5) => request<ForEachPreview>(`/notebook-cells/${enc(cellId)}/preview`, { query: { n } }),

  // runs
  run: (id: string, body: RunCreate = {}) => request<Run>(`/notebooks/${enc(id)}/runs`, { method: 'POST', body }),
  getRun: (runId: string) => request<Run>(`/notebook-runs/${enc(runId)}`),
  cancel: (runId: string) => request<Run>(`/notebook-runs/${enc(runId)}/cancel`, { method: 'POST', body: {} }),
  retryRows: (cellId: string, body: RetryRowsRequest = {}) => request<Run>(`/notebook-cells/${enc(cellId)}/retry-rows`, { method: 'POST', body }),

  // human steps
  apply: (cellId: string, body: ApplyRequest) => request<CellActionResponse>(`/notebook-cells/${enc(cellId)}/apply`, { method: 'POST', body }),
  discard: (cellId: string, body: DiscardRequest) => request<CellActionResponse>(`/notebook-cells/${enc(cellId)}/discard`, { method: 'POST', body }),

  // capabilities / templates / ask
  capabilities: () => request<Capabilities>('/notebooks/capabilities'),
  templates: () => request<NotebookTemplate[]>('/notebook-templates'),
  ask: (id: string, text: string, by?: string, selection?: AskSelection | null) =>
    request<AskResponse>(`/notebooks/${enc(id)}/ask`, { method: 'POST', body: { text, by, ...(selection ? { selection } : {}) } }),

  // API testing: per-notebook environments, import from cURL / the API catalog
  environments: (id: string) => request<NotebookEnvironments>(`/notebooks/${enc(id)}/environments`),
  putEnvironments: (id: string, body: NotebookEnvironments) => request<NotebookEnvironments>(`/notebooks/${enc(id)}/environments`, { method: 'PUT', body }),
  importHttp: (id: string, body: ImportHttpRequest) => request<ImportHttpResponse>(`/notebooks/${enc(id)}/import-http`, { method: 'POST', body }),

  // browser cell
  frame: (cellId: string) => requestBlob(`/notebook-cells/${enc(cellId)}/frame`),
  artifacts: (cellId: string) => request<ArtifactInfo[]>(`/notebook-cells/${enc(cellId)}/artifacts`),
  artifactUrl: (cellId: string, name: string) => apiUrl(`/notebook-cells/${enc(cellId)}/artifacts/${enc(name)}`),
  control: (cellId: string, action: ControlAction) => request<unknown>(`/notebook-cells/${enc(cellId)}/control`, { method: 'POST', body: { action } }),
  /** take over: relay one click / typed text / key / scroll to the live page (the cell must be taken over) */
  input: (cellId: string, body: CellInput) => request<unknown>(`/notebook-cells/${enc(cellId)}/input`, { method: 'POST', body }),

  // report / trace
  report: (id: string) => request<ReportResponse>(`/notebooks/${enc(id)}/report`),
  trace: (id: string) => request<TraceEntry[]>(`/notebooks/${enc(id)}/trace`),

  // keep
  keepPreview: (id: string, body: KeepRequest) => request<KeepPreview>(`/notebooks/${enc(id)}/keep/preview`, { method: 'POST', body }),
  keep: (id: string, body: KeepRequest) => request<KeepResponse>(`/notebooks/${enc(id)}/keep`, { method: 'POST', body }),
  checks: (id: string) => request<ChecksResponse>(`/notebooks/${enc(id)}/checks`),
  exportSpec: (id: string) => requestText(`/notebooks/${enc(id)}/export.spec.ts`),
}
