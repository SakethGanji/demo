/**
 * API seed helpers (UI-DESIGN §12.2): every scenario builds its own state through the API,
 * in its own project with a unique slug `e2e-<test>-<rand>`.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { EngineApi } from './test'
import { rand, stack } from './test'

const HERE = path.dirname(fileURLToPath(import.meta.url))

export interface P {
  id: string
  slug: string
  name: string
  default_environment: string
  live_environment: string
}

export const REFUND_COLUMNS = [
  { name: 'reason', type: 'string', required: true },
  { name: 'amount', type: 'number', required: true },
]

/** A fixture file with `${ENV}` interpolated the same way the fake LLM does. */
export function fixture(rel: string): string {
  const s = stack()
  const env: Record<string, string> = { E2E_UPSTREAM_URL: s.upstreamUrl }
  return fs.readFileSync(path.join(HERE, rel), 'utf8').replace(/\$\{(\w+)\}/g, (_, k: string) => {
    if (!(k in env)) throw new Error(`unknown fixture variable ${k}`)
    return env[k]
  })
}

export async function seedProject(api: EngineApi, test: string, name?: string): Promise<P> {
  const slug = `e2e-${test.toLowerCase()}-${rand()}`
  return api.post<P>('/projects', { name: name ?? `E2E ${test} ${slug.slice(-6)}`, slug })
}

export async function seedSecret(api: EngineApi, p: P, key: string, value: string, envs: string[], hosts?: string[]) {
  const out = []
  for (const environment of envs)
    out.push(
      await api.post<{ id: number }>(`/projects/${p.id}/secrets`, {
        key,
        value,
        environment,
        type: 'secret',
        ...(hosts ? { allowed_hosts: hosts } : {}),
      }),
    )
  return out
}

export function seedTable(api: EngineApi, p: P, name: string, columns = REFUND_COLUMNS) {
  return api.post<{ id: string; name: string }>(`/projects/${p.id}/tables`, { name, columns })
}

export async function seedRows(api: EngineApi, p: P, table: string, rows: Array<Record<string, unknown>>) {
  for (const r of rows) await api.post(`${await api.tablePath(p.id, table)}/rows`, { data: r })
}

export interface SeededModule {
  module: { id: string; name: string; source_sha: string }
  routes: { created: Array<{ id: string; name: string; method: string; path: string }> }
}

export function seedModule(api: EngineApi, p: P, name: string, file: string) {
  return api.post<SeededModule>(`/projects/${p.id}/modules`, { name, source: fixture(`modules/${file}`) })
}

export function seedModuleSource(api: EngineApi, p: P, name: string, source: string) {
  return api.post<SeededModule>(`/projects/${p.id}/modules`, { name, source })
}

/** The standard refunds backend: PAYMENTS_TOKEN (dev, host 127.0.0.1), table, saved refunds.py. */
export async function seedRefunds(api: EngineApi, p: P, opts: { secretEnvs?: string[]; secretValue?: string } = {}) {
  const value = opts.secretValue ?? `sk_test_${rand(10)}${rand(10)}`
  await seedSecret(api, p, 'PAYMENTS_TOKEN', value, opts.secretEnvs ?? ['dev'], ['127.0.0.1'])
  await seedTable(api, p, 'refunds')
  const mod = await seedModule(api, p, 'refunds', 'refunds.py')
  const routes = await api.get<Array<{ id: string; name: string; method: string; path: string; auth: string }>>(`/projects/${p.id}/routes`)
  return { value, mod, routes, routeId: (n: string) => routes.find((r) => r.name === n)!.id }
}

export const BLANK_DEFINITION = { version: 1, components: [] }

export async function seedApp(
  api: EngineApi,
  p: P,
  name: string,
  files: Array<{ path: string; content: string }>,
  routeIds: string[],
) {
  const app = await api.post<{ id: string; definition: Record<string, unknown> }>(`/projects/${p.id}/apps`, { name, definition: BLANK_DEFINITION })
  await api.put(`/apps/${app.id}`, {
    definition: { ...app.definition, studio: { route_ids: routeIds } },
    source_code: files.find((f) => f.path === 'App.tsx')?.content, // api-usage scans the entry source
    files,
    create_version: true,
    version_trigger: 'manual',
  })
  return app
}

export function seedPublished(api: EngineApi, appId: string, slug: string, access: 'public' | 'password' | 'private' = 'public', password?: string) {
  return api.post<{ slug: string; public_url: string }>(`/apps/${appId}/publish`, { slug, access, ...(password ? { access_password: password } : {}) })
}

export async function seedRelease(api: EngineApi, p: P) {
  const snap = await api.post<{ id: string; number: number }>(`/projects/${p.id}/releases`, { environment: p.default_environment })
  return api.post<{ id: string; number: number }>(`/projects/${p.id}/releases/${snap.id}/promote`, { to_environment: p.live_environment })
}

export function seedWorkflow(api: EngineApi, p: P, def: Record<string, unknown>) {
  return api.post<{ id: string; name: string }>(`/projects/${p.id}/workflows`, def)
}

/** `Start → Set {answer: {{ 20 + 22 }}}` (T40). */
export function answerWorkflow(name = 'Answer') {
  return {
    name,
    nodes: [
      { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
      {
        name: 'Set',
        type: 'Set',
        parameters: { mode: 'manual', fields: [{ name: 'answer', value: '{{ 20 + 22 }}', type: 'number' }] },
        position: { x: 240, y: 0 },
      },
    ],
    connections: [{ source_node: 'Start', source_output: 'main', target_node: 'Set', target_input: 'main' }],
  }
}

export function refundsAppFile() {
  return { path: 'App.tsx', content: fixture('apps/RefundsApp.tsx') }
}
