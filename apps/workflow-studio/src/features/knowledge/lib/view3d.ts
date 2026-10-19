/**
 * Choosing between the 3D and the 2D graph: whether WebGL works here, whether this is a desktop, and the reader's
 * last choice (and the team page's "Group by project"), remembered in localStorage. Every storage access is wrapped:
 * private windows and blocked site data just fall back to the defaults.
 */

let webgl: boolean | null = null

/** True when this browser can make a WebGL context. Checked once per page; `markWebGLBroken` turns it off. */
export function hasWebGL(): boolean {
  if (webgl !== null) return webgl
  try {
    const c = document.createElement('canvas')
    const ctx = c.getContext('webgl2') ?? c.getContext('webgl')
    webgl = !!ctx
    ;(ctx as WebGLRenderingContext | null)?.getExtension('WEBGL_lose_context')?.loseContext()
  } catch {
    webgl = false
  }
  return webgl
}

/** The renderer failed to start (a lost or blocked context): stay in 2D for the rest of the page's life. */
export function markWebGLBroken() {
  webgl = false
}

/** A wide screen with a fine pointer. */
export function isDesktop(): boolean {
  try {
    return window.matchMedia('(min-width: 1024px) and (pointer: fine)').matches
  } catch {
    return false
  }
}

const MODE_KEY = 'studio.knowledge.graph-mode'
const GROUP_KEY = 'studio.knowledge.group-by-project'

export type GraphMode = '3d' | 'graph'

export function readGraphMode(): GraphMode | null {
  try {
    const v = window.localStorage.getItem(MODE_KEY)
    return v === '3d' || v === 'graph' ? v : null
  } catch {
    return null
  }
}

export function writeGraphMode(m: GraphMode) {
  try {
    window.localStorage.setItem(MODE_KEY, m)
  } catch {
    /* storage blocked: the URL still carries the choice */
  }
}

/** The graph lens to open when the URL names none: the last choice, else 3D on a desktop with WebGL, else 2D. */
export function defaultGraphMode(): GraphMode {
  const want = readGraphMode() ?? (isDesktop() ? '3d' : 'graph')
  return want === '3d' && !hasWebGL() ? 'graph' : want
}

export function readGroupByProject(): boolean {
  try {
    return window.localStorage.getItem(GROUP_KEY) === '1'
  } catch {
    return false
  }
}

export function writeGroupByProject(on: boolean) {
  try {
    window.localStorage.setItem(GROUP_KEY, on ? '1' : '0')
  } catch {
    /* storage blocked: the URL still carries the choice */
  }
}
