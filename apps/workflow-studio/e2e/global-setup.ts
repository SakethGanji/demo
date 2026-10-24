/**
 * Isolated E2E stack (UI-DESIGN §12.1):
 *  1. throwaway DB `workflows_test_e2e_<hex>` via apps/workflow-engine/tests/e2e_stack.py
 *  2. a local upstream HTTP server (balance, leak, webhook, request log)
 *  3. the engine on its own port with an allow-listed environment (fake LLM, no provider keys,
 *     dotenv off, fresh tmp cwd) — never the developer .env
 *  4. its own Vite instance pointed at that engine
 *  5. guards: ports ≠ 8000/5173, DB name, `/api/_test/health`, only `proj_default` exists
 * The live :8000 engine, :5173 studio and `workflows` database are never touched.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const STUDIO_DIR = path.resolve(HERE, '..')
const ENGINE_DIR = path.resolve(STUDIO_DIR, '../workflow-engine')
const VENV_PY = path.join(ENGINE_DIR, 'venv/bin/python')
/** per run (the main process's pid): two concurrent `test:e2e` runs must not read or delete each other's stack */
export const STATE_FILE = process.env.E2E_STATE ?? path.join(HERE, '.stack', `state-${process.pid}.json`)

export interface StackState {
  dbName: string
  engineUrl: string
  studioUrl: string
  upstreamUrl: string
  token: string
  tmp: string
  enginePid: number
  vitePid: number
  scriptPath: string
  refund_workflow_id: string
  refund_failed_execution_id: string
  refund_table_id: string
  fake_api_url: string
  fake_api_ok_url: string
  fake_api_fail_url: string
  fake_api_pid: number
  /** notebook specs: the bank DB + its connections (nbseed), and the optional jobs-service (browser cells) */
  nb_db_name?: string
  nb_conn_bank?: string
  nb_conn_bank_rw?: string
  jobsUrl?: string | null
  jobsPid?: number | null
  jobsSkip?: string | null
}

const NO_PORTS = new Set([8000, 8010, 8095, 5173, 5174])

/** apps/jobs-service on a free port (JOBS_DEV=1, JOBS_LLM=off, tmp data dir), for notebook browser cells. Optional:
 *  returns why it was skipped instead of failing the stack. */
async function startJobs(tmp: string): Promise<{ url: string; pid: number } | { skip: string }> {
  const dir = path.resolve(STUDIO_DIR, '../jobs-service')
  if (!fs.existsSync(path.join(dir, 'src/server.ts'))) return { skip: 'apps/jobs-service is missing' }
  const pwRoot = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(os.homedir(), '.cache/ms-playwright')
  if (!fs.existsSync(pwRoot) || !fs.readdirSync(pwRoot).some((d) => d.startsWith('chromium'))) return { skip: "Playwright's chromium is not installed" }
  let port = await freePort()
  while (NO_PORTS.has(port)) port = await freePort()
  const dataDir = path.join(tmp, 'jobs-data')
  fs.mkdirSync(dataDir, { recursive: true })
  const log = path.join(tmp, 'jobs.log')
  const out = fs.openSync(log, 'w')
  const env: Record<string, string> = {
    PATH: [path.dirname(process.execPath), '/usr/bin', '/bin'].join(':'),
    HOME: process.env.HOME ?? tmp,
    LANG: 'C.UTF-8',
    JOBS_DEV: '1',
    JOBS_LLM: 'off',
    JOBS_DATA_DIR: dataDir,
    JOBS_PORT: String(port),
    JOBS_HOST: '127.0.0.1',
  }
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) env.PLAYWRIGHT_BROWSERS_PATH = process.env.PLAYWRIGHT_BROWSERS_PATH
  const proc = spawn(process.execPath, ['src/server.ts'], { cwd: dir, env, detached: true, stdio: ['ignore', out, out] })
  const url = `http://127.0.0.1:${port}`
  try {
    await waitFor(`${url}/health`, 30_000, {}, proc, log)
  } catch (e) {
    try {
      process.kill(-proc.pid!, 'SIGTERM')
    } catch {
      /* gone */
    }
    return { skip: `jobs-service did not start: ${String(e).slice(0, 300)}` }
  }
  return { url, pid: proc.pid! }
}

async function freePort(min = 0): Promise<number> {
  for (;;) {
    const port = await new Promise<number>((resolve, reject) => {
      const s = net.createServer()
      s.once('error', reject)
      s.listen(min ? min + Math.floor(Math.random() * 2000) : 0, '127.0.0.1', () => {
        const p = (s.address() as net.AddressInfo).port
        s.close(() => resolve(p))
      })
    }).catch(() => 0)
    if (port && port !== 8000 && port !== 5173) return port
  }
}

async function waitFor(url: string, ms: number, headers: Record<string, string> = {}, proc?: ChildProcess, log?: string) {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    if (proc && proc.exitCode !== null) throw new Error(`process exited early (${proc.exitCode}); log:\n${log ? fs.readFileSync(log, 'utf8').slice(-4000) : ''}`)
    try {
      const r = await fetch(url, { headers })
      if (r.ok) return r
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`timed out waiting for ${url}${log ? `; log:\n${fs.readFileSync(log, 'utf8').slice(-4000)}` : ''}`)
}

// ── upstream (step 2) ─────────────────────────────────────────────────────────
interface UpstreamReq {
  method: string
  path: string
  headers: Record<string, string | string[] | undefined>
  body: string
}
function startUpstream(port: number): http.Server {
  const log: UpstreamReq[] = []
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      const url = new URL(req.url ?? '/', 'http://x')
      if (url.pathname === '/__log') {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify(log))
        return
      }
      if (url.pathname === '/__reset') {
        log.length = 0
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end('{"ok":true}')
        return
      }
      log.push({ method: req.method ?? 'GET', path: url.pathname, headers: req.headers, body })
      if (log.length > 2000) log.shift()
      const auth = String(req.headers.authorization ?? '')
      if (url.pathname === '/v1/balance') {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ available: 12840.25 }))
      } else if (url.pathname === '/v1/leak') {
        // the secret comes back in the body, a header and (with ?fail=1) a 500 error text (T38)
        if (url.searchParams.get('fail') === '1') {
          res.writeHead(500, { 'content-type': 'text/plain', 'x-echo': auth })
          res.end(`upstream failed for ${auth}`)
        } else {
          res.writeHead(200, { 'content-type': 'application/json', 'x-echo': auth })
          res.end(JSON.stringify({ echo: auth }))
        }
      } else {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ method: req.method, path: url.pathname, headers: req.headers, body }))
      }
    })
  })
  server.listen(port, '127.0.0.1')
  return server
}

export default async function globalSetup() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'luna-e2e-'))
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true })

  // 1. database
  const dbName = `workflows_test_e2e_${crypto.randomBytes(4).toString('hex')}`
  if (!/^workflows_test_e2e_[0-9a-f]{8}$/.test(dbName)) throw new Error('bad db name')
  const up = execFileSync(VENV_PY, ['tests/e2e_stack.py', 'up'], {
    cwd: ENGINE_DIR,
    env: { PATH: `${path.join(ENGINE_DIR, 'venv/bin')}:/usr/bin:/bin`, HOME: tmp, TEST_DB_NAME: dbName, LANG: 'C.UTF-8' },
    encoding: 'utf8',
  })
  const db = JSON.parse(up.trim().split('\n').pop()!) as { db_name: string; db_host: string; db_port: number | string; db_user: string; db_password: string }
  if (db.db_name !== dbName) throw new Error(`e2e_stack created ${db.db_name}, expected ${dbName}`)

  // 2. upstream
  const upstreamPort = await freePort()
  const upstream = startUpstream(upstreamPort)
  ;(globalThis as Record<string, unknown>).__e2eUpstream = upstream
  const upstreamUrl = `http://127.0.0.1:${upstreamPort}`

  // 3. engine with an allow-listed environment
  const enginePort = await freePort()
  const esbuild = fs.realpathSync(execFileSync('which', ['esbuild'], { encoding: 'utf8' }).trim())
  const token = crypto.randomBytes(24).toString('hex') // 48 chars
  const scriptPath = path.join(HERE, 'fixtures', 'llm-script.json')
  const fernet = execFileSync(VENV_PY, ['-c', 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())'], {
    env: { PATH: '/usr/bin:/bin', HOME: tmp },
    encoding: 'utf8',
  }).trim()
  const jobs = await startJobs(tmp)
  if ('skip' in jobs) console.log(`[e2e] jobs-service skipped: ${jobs.skip}`)
  const engineCwd = path.join(tmp, 'engine-cwd')
  // the SDK cache lives under TMPDIR=<tmp>; sandbox jobs use short /tmp/sbx-* dirs (AF_UNIX path limit)
  fs.mkdirSync(engineCwd, { recursive: true })
  const engineEnv: Record<string, string> = {
    PATH: [path.join(ENGINE_DIR, 'venv/bin'), path.dirname(esbuild), path.dirname(process.execPath), '/usr/bin', '/bin'].join(':'),
    HOME: tmp,
    TMPDIR: tmp,
    LANG: 'C.UTF-8',
    WORKFLOW_DB_HOST: String(db.db_host),
    WORKFLOW_DB_PORT: String(db.db_port),
    WORKFLOW_DB_USER: String(db.db_user),
    WORKFLOW_DB_PASSWORD: String(db.db_password),
    WORKFLOW_DB_NAME: dbName,
    WORKFLOW_DISABLE_DOTENV: '1',
    WORKFLOW_TEST_MODE: '1',
    WORKFLOW_BIND_HOST: '127.0.0.1',
    WORKFLOW_LLM_PROVIDER: 'fake',
    WORKFLOW_FAKE_LLM_SCRIPT: scriptPath,
    WORKFLOW_FAKE_LLM_TOKEN: token,
    WORKFLOW_ENCRYPTION_KEY: fernet,
    WORKFLOW_PUBLIC_TOKEN_SECRET: crypto.randomBytes(24).toString('hex'),
    WORKFLOW_RELOAD: 'false',
    WORKFLOW_LOG_LEVEL: 'warning',
    SEED: '0',
    E2E_UPSTREAM_URL: upstreamUrl,
    WORKFLOW_NOTEBOOK_STORAGE_DIR: path.join(tmp, 'nb-store'),
    ...('url' in jobs ? { JOBS_SERVICE_URL: jobs.url, JOBS_DEV: '1' } : {}),
  }
  for (const k of Object.keys(engineEnv)) if (/^(ANTHROPIC|OPENAI|GOOGLE|GEMINI)_/.test(k)) throw new Error(`provider key ${k} in env`)
  const engineLog = path.join(tmp, 'engine.log')
  const engineOut = fs.openSync(engineLog, 'w')
  const engine = spawn(VENV_PY, ['-m', 'uvicorn', 'src.main:app', '--app-dir', ENGINE_DIR, '--host', '127.0.0.1', '--port', String(enginePort)], {
    cwd: engineCwd,
    env: engineEnv,
    detached: true,
    stdio: ['ignore', engineOut, engineOut],
  })
  const engineUrl = `http://127.0.0.1:${enginePort}`
  await waitFor(`${engineUrl}/health`, 90_000, {}, engine, engineLog)

  // 5. guards
  if (enginePort === 8000) throw new Error('refusing engine on :8000')
  const health = (await (await waitFor(`${engineUrl}/api/_test/health`, 10_000, { 'X-Test-Token': token })).json()) as { fake_llm: boolean; db: string }
  if (health.fake_llm !== true || health.db !== dbName) throw new Error(`engine health guard failed: ${JSON.stringify(health)}`)
  const projects = (await (await fetch(`${engineUrl}/api/projects`)).json()) as Array<{ id: string }>
  if (projects.length !== 1 || projects[0].id !== 'proj_default') throw new Error(`fresh DB expected, got ${projects.map((p) => p.id)}`)

  // 5b. seed "Refund triage" + one failed run + the fake API (Stage 1 editor specs)
  const seeded = JSON.parse(
    execFileSync(VENV_PY, ['tests/e2e_stack.py', 'seed', '--engine-url', engineUrl], {
      cwd: ENGINE_DIR,
      env: { PATH: `${path.join(ENGINE_DIR, 'venv/bin')}:/usr/bin:/bin`, HOME: tmp, LANG: 'C.UTF-8' },
      encoding: 'utf8',
      // the fake API server is detached and inherits the seed's stdio; ignoring stdin/stderr keeps execFileSync from waiting on it
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim().split('\n').pop()!,
  ) as Pick<StackState, 'refund_workflow_id' | 'refund_failed_execution_id' | 'refund_table_id' | 'fake_api_url' | 'fake_api_ok_url' | 'fake_api_fail_url' | 'fake_api_pid'>

  // 5c. notebook specs: a bank DB + connections bank / bank_rw in proj_default
  const nbSeeded = JSON.parse(
    execFileSync(VENV_PY, ['tests/e2e_stack.py', 'nbseed', '--engine-url', engineUrl], {
      cwd: ENGINE_DIR,
      env: { PATH: `${path.join(ENGINE_DIR, 'venv/bin')}:/usr/bin:/bin`, HOME: tmp, LANG: 'C.UTF-8' },
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'inherit'],
    }).trim().split('\n').pop()!,
  ) as Pick<StackState, 'nb_db_name' | 'nb_conn_bank' | 'nb_conn_bank_rw'>

  // 4. studio
  const vitePort = await freePort(5180)
  if (vitePort === 5173) throw new Error('refusing vite on :5173')
  const viteLog = path.join(tmp, 'vite.log')
  const viteOut = fs.openSync(viteLog, 'w')
  const vite = spawn(path.join(STUDIO_DIR, 'node_modules/.bin/vite'), ['--port', String(vitePort), '--strictPort', '--host', '127.0.0.1'], {
    cwd: STUDIO_DIR,
    env: { PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: process.env.HOME ?? tmp, VITE_WORKFLOW_API_URL: engineUrl },
    detached: true,
    stdio: ['ignore', viteOut, viteOut],
  })
  const studioUrl = `http://127.0.0.1:${vitePort}`
  await waitFor(`${studioUrl}/`, 60_000, {}, vite, viteLog)
  // warm the dependency optimiser so the first test doesn't pay for it
  await fetch(`${studioUrl}/src/app/main.tsx`).catch(() => undefined)

  const state: StackState = {
    dbName,
    engineUrl,
    studioUrl,
    upstreamUrl,
    token,
    tmp,
    enginePid: engine.pid!,
    vitePid: vite.pid!,
    scriptPath,
    ...seeded,
    ...nbSeeded,
    jobsUrl: 'url' in jobs ? jobs.url : null,
    jobsPid: 'url' in jobs ? jobs.pid : null,
    jobsSkip: 'skip' in jobs ? jobs.skip : null,
  }
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2))
  process.env.E2E_STATE = STATE_FILE
  console.log(`[e2e] db=${dbName} engine=${engineUrl} studio=${studioUrl} upstream=${upstreamUrl} logs=${tmp}`)
}
