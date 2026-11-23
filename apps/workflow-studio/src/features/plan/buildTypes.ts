/**
 * A build run: the engine's coordinator builds an approved plan version end to end (backend modules in build order,
 * then, after any input it asks for, the apps, then up to two verify rounds), one step at a time. These types match the builds API exactly.
 */

export type BuildStatus = 'queued' | 'running' | 'waiting_for_input' | 'done' | 'partial' | 'failed' | 'cancelled'
export type StepStatus = 'pending' | 'running' | 'waiting_for_input' | 'passed' | 'partial' | 'failed' | 'skipped'

/** What an `input` step needs before the build goes on (between the backend and the UI): secrets and starting data. */
export interface BuildInput {
  secrets: Array<{ name: string; purpose: string; set: boolean }>
  tables: Array<{ name: string; columns: string[]; rows: number; suggested_min: number }>
  message: string
  /** the environment the secrets are checked in (the project's draft environment) */
  environment?: string
}

export interface BuildStep {
  /** "module:runs", "app:workbench", "verify:1", "fix:runs:1" */
  key: string
  kind: 'module' | 'app' | 'verify' | 'fix' | 'input'
  /** "Backend · Runs" */
  title: string
  builder: 'backend' | 'ui' | 'check'
  status: StepStatus
  attempt: number
  started_at: string | null
  finished_at: string | null
  /** one line, plain words */
  summary: string | null
  problems: Array<{ code: string; message: string }>
  module_id?: string | null
  app_id?: string | null
  /** set on an `input` step while it waits */
  input?: BuildInput | null
  /** the last ~50 progress lines (only on GET of one build, and appended from `log` events) */
  log?: string[]
}

export interface BuildReport {
  built: string[]
  /** saved, with problems left (older builds list these under `failed`) */
  partial?: string[]
  failed: string[]
  plan_check: { ok: boolean; missing: Record<string, unknown[]> }
  app_ids: string[]
}

export interface Build {
  id: string
  project_id: string
  plan_version: number
  app_key: string | null
  status: BuildStatus
  reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  steps: BuildStep[]
  report: null | BuildReport
}

export interface StartBuild {
  app_key?: string
  rebuild?: boolean
}

/** Who wrote a file during a build. */
export type FileBuilder = 'backend' | 'ui' | 'plan' | 'tables'

/** A planned route's state as the build goes: announced at the start, built by its module, then its self-test. */
export type RouteStatus = 'stub' | 'built' | 'passing' | 'failing'

/**
 * One SSE `data:` payload of `/builds/{id}/events`. The stream starts with a snapshot and ends after `done`. In between,
 * besides steps and progress lines, the files the builders write (`file`: PLAN.md once at the start; a module as drafts,
 * `done: false`, then its saved source; each table after create/migrate; each app file; a delete has empty content;
 * content past 200 KB is cut, `truncated`), each app save (`preview`) and each planned route's state (`route`, every
 * planned route first announced as `stub`).
 */
export type BuildEvent =
  | { type: 'snapshot'; build: Build }
  | { type: 'step'; step: BuildStep }
  | { type: 'log'; step_key: string; message: string }
  | { type: 'done'; build: Build }
  | {
      type: 'file'
      step_key: string | null
      builder: FileBuilder
      path: string
      op: 'write' | 'delete'
      content: string
      done: boolean
      truncated: boolean
    }
  | { type: 'preview'; app_id: string; version_id: string | null }
  | { type: 'route'; step_key: string | null; method: string; path: string; name: string; status: RouteStatus }
