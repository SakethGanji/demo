/** Helpers for specs 17 and 18: screenshots into a fixed folder, the fake LLM script swap, and an own fake risk API. */
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { test, type EngineApi } from './test'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const SHOTS = '/tmp/claude-1000/-home-saketh-Projects-playground-work-demo/3b980f2a-bdc6-4187-b5dc-d2b1e0bdc78d/scratchpad/e2e-shots'
const ENGINE_DIR = path.resolve(HERE, '../../../workflow-engine')

export async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true })
  const file = test.info().outputPath(`${name}.png`)
  await page.screenshot({ path: file })
  fs.copyFileSync(file, path.join(SHOTS, `${name}.png`))
}

export const BASE_SCRIPT = (): { rules: unknown[]; [k: string]: unknown } =>
  JSON.parse(fs.readFileSync(path.join(HERE, 'llm-script.json'), 'utf8'))

/** Put `rules` in front of the stack's pinned script (so existing rules keep working). */
export async function putLlmRules(api: EngineApi, rules: unknown[]) {
  const base = BASE_SCRIPT()
  await api.testApi('PUT', 'fake-llm/script', { ...base, rules: [...rules, ...base.rules] })
  await api.clearLlmCalls()
}
export async function restoreLlmScript(api: EngineApi) {
  await api.testApi('PUT', 'fake-llm/script', BASE_SCRIPT())
}

/** tests/fake_refund_api.py on its own port (so a v2 switch cannot leak into other specs). Kill it by PID. */
export class OwnFakeApi {
  proc!: ChildProcess
  url = ''
  async start() {
    this.proc = spawn(path.join(ENGINE_DIR, 'venv/bin/python'), [path.join(ENGINE_DIR, 'tests/fake_refund_api.py')], {
      detached: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    const port = await new Promise<number>((resolve, reject) => {
      let buf = ''
      const t = setTimeout(() => reject(new Error('fake api did not start')), 15_000)
      this.proc.stdout!.on('data', (c) => {
        buf += c
        const line = buf.split('\n')[0]
        if (buf.includes('\n')) {
          clearTimeout(t)
          resolve(JSON.parse(line).port)
        }
      })
    })
    this.url = `http://127.0.0.1:${port}`
  }
  async shape(version: 'v1' | 'v2') {
    const r = await fetch(`${this.url}/_shape`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ version }) })
    if (!r.ok) throw new Error(`shape ${version}: ${r.status}`)
  }
  stop() {
    try {
      process.kill(-this.proc.pid!, 'SIGTERM')
    } catch {
      /* gone */
    }
  }
}
