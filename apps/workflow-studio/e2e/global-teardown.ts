/**
 * Teardown (UI-DESIGN §12.1 step 7): fail the run if the fake LLM answered any call with no
 * matching rule (`rule_id: null`), stop the engine and Vite process groups, close the upstream
 * and drop the DB unless KEEP_E2E=1.
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type http from 'node:http'
import { STATE_FILE, type StackState } from './global-setup'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ENGINE_DIR = path.resolve(HERE, '../../workflow-engine')

function killGroup(pid: number) {
  try {
    process.kill(-pid, 'SIGTERM')
  } catch {
    /* already gone */
  }
}

export default async function globalTeardown() {
  if (!fs.existsSync(STATE_FILE)) return
  const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')) as StackState
  let unmatched: unknown[] = []
  try {
    const calls = (await (await fetch(`${s.engineUrl}/api/_test/fake-llm/calls`, { headers: { 'X-Test-Token': s.token } })).json()) as Array<{ rule_id: string | null }>
    unmatched = calls.filter((c) => c.rule_id === null)
  } catch (e) {
    console.error('[e2e] could not read the fake-LLM call log', e)
  }
  if (process.env.KEEP_E2E === '1') {
    console.log(`[e2e] KEEP_E2E=1: engine ${s.engineUrl} (pid ${s.enginePid}), studio ${s.studioUrl} (pid ${s.vitePid}), db ${s.dbName}, logs ${s.tmp}`)
  } else {
    killGroup(s.enginePid)
    killGroup(s.vitePid)
    if (s.fake_api_pid) killGroup(s.fake_api_pid)
    if (s.jobsPid) killGroup(s.jobsPid)
    await new Promise((r) => setTimeout(r, 1500))
    const upstream = (globalThis as Record<string, unknown>).__e2eUpstream as http.Server | undefined
    upstream?.close()
    execFileSync(path.join(ENGINE_DIR, 'venv/bin/python'), ['tests/e2e_stack.py', 'down', '--db', s.dbName, ...(s.nb_db_name ? ['--nbdb', s.nb_db_name] : [])], {
      cwd: ENGINE_DIR,
      env: { PATH: `${path.join(ENGINE_DIR, 'venv/bin')}:/usr/bin:/bin`, HOME: s.tmp, LANG: 'C.UTF-8' },
      stdio: 'inherit',
    })
    fs.rmSync(STATE_FILE, { force: true })
  }
  if (unmatched.length) throw new Error(`[e2e] the fake LLM answered ${unmatched.length} call(s) with no matching rule: ${JSON.stringify(unmatched).slice(0, 2000)}`)
}
