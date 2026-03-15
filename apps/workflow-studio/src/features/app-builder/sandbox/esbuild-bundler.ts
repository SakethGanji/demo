/**
 * In-browser bundler using esbuild-wasm.
 *
 * Bundles multiple TSX/TS files from a virtual filesystem into a single
 * IIFE that sets `window.__AppModule` with a `default` export.
 * React and ReactDOM are mapped to window globals (loaded via CDN in the iframe).
 *
 * The esbuild WASM binary is served from our own static assets (copied to
 * public/ at install time) so it works behind corporate firewalls.
 */

import * as esbuild from 'esbuild-wasm'

export interface AppFile {
  path: string
  content: string
}

// ---------------------------------------------------------------------------
// Lazy initialisation — WASM binary served from our own origin
// ---------------------------------------------------------------------------

let initPromise: Promise<void> | null = null

function ensureInit(): Promise<void> {
  if (!initPromise) {
    initPromise = esbuild.initialize({
      wasmURL: '/esbuild.wasm',
    })
  }
  return initPromise
}

// ---------------------------------------------------------------------------
// Path helpers (no node `path` module available in the browser)
// ---------------------------------------------------------------------------

function dirname(p: string): string {
  const i = p.lastIndexOf('/')
  return i < 0 ? '' : p.slice(0, i)
}

function join(...parts: string[]): string {
  const segments: string[] = []
  for (const part of parts) {
    for (const seg of part.split('/')) {
      if (seg === '..') segments.pop()
      else if (seg && seg !== '.') segments.push(seg)
    }
  }
  return segments.join('/')
}

// ---------------------------------------------------------------------------
// Virtual filesystem plugin
// ---------------------------------------------------------------------------

const EXTENSIONS = ['.tsx', '.ts', '.jsx', '.js']
const INDEX_FILES = EXTENSIONS.map((e) => `index${e}`)

/** Map of global externals — these come from CDN scripts, not the bundle. */
const GLOBAL_EXTERNALS: Record<string, string> = {
  react: 'window.React',
  'react-dom': 'window.ReactDOM',
  'react-dom/client': 'window.ReactDOM',
}

function resolveFile(fileMap: Map<string, AppFile>, rawPath: string): string | undefined {
  // Exact match
  if (fileMap.has(rawPath)) return rawPath
  // Try extensions
  for (const ext of EXTENSIONS) {
    const withExt = rawPath + ext
    if (fileMap.has(withExt)) return withExt
  }
  // Try as directory with index file
  for (const idx of INDEX_FILES) {
    const asDir = rawPath + '/' + idx
    if (fileMap.has(asDir)) return asDir
  }
  return undefined
}

function virtualFsPlugin(files: AppFile[]): esbuild.Plugin {
  const fileMap = new Map(files.map((f) => [f.path, f]))

  return {
    name: 'virtual-fs',
    setup(build) {
      // Global externals (react, react-dom) → shimmed to window globals
      build.onResolve({ filter: /^react(-dom)?(\/.*)?$/ }, (args) => ({
        path: args.path,
        namespace: 'global-external',
      }))

      build.onLoad({ filter: /.*/, namespace: 'global-external' }, (args) => {
        const globalRef = GLOBAL_EXTERNALS[args.path] ?? 'undefined'
        return { contents: `module.exports = ${globalRef}`, loader: 'js' }
      })

      // Relative imports → resolve in virtual filesystem
      build.onResolve({ filter: /^\./ }, (args) => {
        const dir = args.importer ? dirname(args.importer) : ''
        const resolved = resolveFile(fileMap, join(dir, args.path))
        if (resolved) return { path: resolved, namespace: 'virtual' }
        return { errors: [{ text: `Could not resolve "${args.path}" from "${args.importer}"` }] }
      })

      // Entry point / bare path → resolve in virtual filesystem
      build.onResolve({ filter: /.*/ }, (args) => {
        if (args.namespace === 'virtual' || args.namespace === 'global-external') return undefined
        const resolved = resolveFile(fileMap, args.path)
        if (resolved) return { path: resolved, namespace: 'virtual' }
        return undefined
      })

      // Load file contents from memory
      build.onLoad({ filter: /.*/, namespace: 'virtual' }, (args) => {
        const file = fileMap.get(args.path)
        if (!file) return { errors: [{ text: `File not found: ${args.path}` }] }
        const ext = (args.path.split('.').pop() ?? 'tsx') as esbuild.Loader
        return { contents: file.content, loader: ext }
      })
    },
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface BundleResult {
  code: string
  css: string
  errors: string[]
}

const ENTRY_NAMES = ['App', 'app', 'index']
const ENTRY_DIRS = ['', 'src/']

function findEntryPoint(files: AppFile[]): string {
  const paths = new Set(files.map((f) => f.path))
  // Try each directory prefix + name + extension combination
  for (const dir of ENTRY_DIRS) {
    for (const name of ENTRY_NAMES) {
      for (const ext of EXTENSIONS) {
        const candidate = dir + name + ext
        if (paths.has(candidate)) return candidate
      }
    }
  }
  // Fallback: first .tsx/.ts file
  const first = files.find((f) => /\.[tj]sx?$/.test(f.path))
  return first?.path ?? 'App.tsx'
}

export async function bundleFiles(files: AppFile[]): Promise<BundleResult> {
  await ensureInit()

  const entryPoint = findEntryPoint(files)

  try {
    const result = await esbuild.build({
      entryPoints: [entryPoint],
      bundle: true,
      format: 'iife',
      globalName: '__AppModule',
      jsx: 'transform',
      jsxFactory: 'React.createElement',
      jsxFragment: 'React.Fragment',
      write: false,
      plugins: [virtualFsPlugin(files)],
      define: {
        'process.env.NODE_ENV': '"production"',
      },
    })

    let code = ''
    let css = ''
    for (const out of result.outputFiles ?? []) {
      if (out.path.endsWith('.css')) css += out.text
      else code += out.text
    }

    const errors = result.errors.map((e) => {
      const loc = e.location ? ` (${e.location.file}:${e.location.line})` : ''
      return `${e.text}${loc}`
    })

    return { code, css, errors }
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Bundle failed'
    return { code: '', css: '', errors: [msg] }
  }
}
