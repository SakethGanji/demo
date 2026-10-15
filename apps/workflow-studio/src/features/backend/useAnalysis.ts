/**
 * Live analysis while typing (UI-DESIGN §4.3): a 600 ms debounced dry run of the editor text.
 * Saved modules use `PUT …/modules/{M}?dry_run=true`, new ones `POST …/modules?dry_run=true`.
 * Nothing is created. Errors become CodeMirror diagnostics (§4.4).
 */
import { useEffect, useRef, useState } from 'react'
import type { Diagnostic } from '@codemirror/lint'
import type { Text } from '@codemirror/state'
import { modulesApi } from '@/shared/api/endpoints'
import { ApiError, errorMessage } from '@/shared/api/http'
import type { DryRunResult, PlannedRoute } from '@/shared/api/types'

export interface Problem {
  code: string
  line: number | null
  column: number | null
  message: string
  /** for route_conflict: the route that already owns the path */
  handler?: { type: string; id?: string; name?: string } | null
  existingRouteId?: string | null
}

export interface AnalysisState {
  pending: boolean
  result: DryRunResult | null
  problems: Problem[]
  /** non-diagnostic failure (network, 5xx) */
  error: string | null
  /** the source the result/problems belong to */
  source: string | null
}

/** Normalise a module save/dry-run ApiError into line problems (§4.4, §9 C7). */
export function problemsFromError(e: unknown): Problem[] | null {
  if (!(e instanceof ApiError)) return null
  const d = e.details as {
    errors?: Array<{ line?: number; column?: number | null; message?: string }>
    conflicts?: Array<{ method?: string; path?: string; line?: number; existing_route_id?: string; handler?: Problem['handler']; message?: string }>
    line?: number
  }
  if (e.code === 'route_conflict') {
    const cs = d.conflicts?.length ? d.conflicts : [{ line: d.line, message: e.message }]
    return cs.map((c) => ({
      code: 'route_conflict',
      line: c.line ?? null,
      column: null,
      message: conflictMessage(c, e.message),
      handler: 'handler' in c ? c.handler : null,
      existingRouteId: 'existing_route_id' in c ? (c.existing_route_id ?? null) : null,
    }))
  }
  if (e.code === 'syntax_error' || e.code === 'invalid_decorator' || e.code === 'name_taken') {
    const errs = d.errors?.length ? d.errors : [{ line: d.line, column: null, message: e.message }]
    return errs.map((x) => ({
      code: e.code!,
      line: x.line ?? null,
      column: x.column ?? null,
      message: x.message ?? e.message,
    }))
  }
  return null
}

function conflictMessage(
  c: { method?: string; path?: string; handler?: Problem['handler']; message?: string },
  fallback: string,
): string {
  if (c.method && c.path && c.handler) {
    const who = c.handler.type === 'workflow' ? `workflow '${c.handler.name}'` : c.handler.type === 'python' ? `${c.handler.name ?? 'a module'}` : c.handler.type
    return `${c.method} ${c.path} already exists (${who})`
  }
  return c.message ?? fallback
}

export function problemsToDiagnostics(doc: Text, problems: Problem[]): Diagnostic[] {
  const out: Diagnostic[] = []
  for (const p of problems) {
    if (p.line == null || p.line < 1 || p.line > doc.lines) continue
    const line = doc.line(p.line)
    let from = line.from
    let to = line.to
    if (p.column != null && p.column >= 1) {
      from = Math.min(line.to, line.from + p.column - 1)
      to = Math.min(line.to, from + 1)
      if (to === from) from = Math.max(line.from, from - 1)
    }
    out.push({ from, to: Math.max(to, from), severity: 'error', message: p.message, source: p.code })
  }
  return out
}

export function routesOf(result: DryRunResult | null): PlannedRoute[] {
  if (!result) return []
  return [...result.routes.created, ...result.routes.updated]
}

export function useLiveAnalysis(opts: {
  pid: string
  moduleId: string | null
  moduleName: string
  source: string
  enabled: boolean
  /** skip analysis for the text the editor was loaded with (it is the saved source) */
  skipSource?: string | null
}): AnalysisState & { runNow: () => void } {
  const { pid, moduleId, moduleName, source, enabled, skipSource } = opts
  const [state, setState] = useState<AnalysisState>({ pending: false, result: null, problems: [], error: null, source: null })
  const seq = useRef(0)
  const timer = useRef<number | null>(null)

  const run = (src: string) => {
    const my = ++seq.current
    setState((s) => ({ ...s, pending: true }))
    const p = moduleId
      ? modulesApi.updateDryRun(pid, moduleId, { source: src })
      : modulesApi.createDryRun(pid, { name: moduleName, source: src })
    p.then(
      (result) => {
        if (my === seq.current) setState({ pending: false, result, problems: [], error: null, source: src })
      },
      (e) => {
        if (my !== seq.current) return
        const problems = problemsFromError(e)
        if (problems) setState({ pending: false, result: null, problems, error: null, source: src })
        else setState({ pending: false, result: null, problems: [], error: errorMessage(e), source: src })
      },
    )
  }

  useEffect(() => {
    if (!enabled || !moduleName || moduleName === '~new') return
    if (skipSource != null && source === skipSource) {
      setState({ pending: false, result: null, problems: [], error: null, source: null })
      return
    }
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => run(source), 600)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, moduleId, moduleName, source, enabled, skipSource])

  return { ...state, runNow: () => run(source) }
}
