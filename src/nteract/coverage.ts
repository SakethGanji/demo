/**
 * Coverage bookkeeping for the nteract gallery. Everything here is computed, not hand-counted:
 * - the 35 registry items come from https://nteract-elements.vercel.app/r/registry.json,
 * - component files come from an eager glob of src/components and src/lib (which also loads them all),
 * - widget models come from WIDGET_REGISTRY, filled by the controls and ipycanvas index modules,
 * - "rendered" widget models are reported at runtime by mounted tiles and a DOM scan for data-widget-id.
 */
import { WIDGET_REGISTRY } from '@/components/widgets/widget-registry'
import '@/components/widgets/controls'
import '@/components/widgets/ipycanvas'

export const REGISTRY_ITEMS = [
  'ansi-output', 'image-output', 'html-output', 'svg-output', 'markdown-output', 'json-output', 'media-provider', 'media-router',
  'cell-header', 'cell-type-button', 'execution-status', 'play-button', 'runtime-health-indicator', 'cell-controls', 'cell-container',
  'cell-betweener', 'output-area', 'execution-count', 'collaborator-avatars', 'presence-bookmarks', 'codemirror-editor', 'widget-store',
  'anywidget-view', 'widget-view', 'widget-controls', 'ipycanvas', 'isolated-frame', 'cell-type-selector', 'error-boundary',
  'highlight-text', 'isolated-renderer', 'all', 'global-find-bar', 'runtime-icons', 'selection-card',
] as const
export type RegistryItem = (typeof REGISTRY_ITEMS)[number]

const allModules = import.meta.glob(['../components/**/*.{ts,tsx}', '../lib/*.{ts,tsx}'], { eager: true }) as Record<string, Record<string, unknown>>
const short = (p: string) => p.replace('../components/', '').replace('../lib/', 'lib/')

/** Every installed component/lib file, as `cell/OutputArea.tsx` or `lib/utils.ts`. */
export const ALL_FILES = Object.keys(allModules).map(short).sort()

/** Files whose coverage is decided at runtime: a widget component file counts once one of its models rendered. */
export const WIDGET_FILES: Record<string, string[]> = {}
for (const [path, mod] of Object.entries(allModules)) {
  if (!/widgets\/(controls|ipycanvas)\/[^/]+\.tsx$/.test(path)) continue
  const exports = Object.values(mod)
  WIDGET_FILES[short(path)] = Object.entries(WIDGET_REGISTRY).filter(([, C]) => exports.includes(C)).map(([name]) => name)
}
export const CONTROL_FILES = Object.keys(WIDGET_FILES).filter(f => f.startsWith('widgets/controls/'))

export const registeredModels = () => Object.keys(WIDGET_REGISTRY).sort()

// rendered-model tracking (a tiny external store for useSyncExternalStore)
const rendered = new Set<string>()
const listeners = new Set<() => void>()
let version = 0
export function markRendered(names: Iterable<string>) {
  let changed = false
  for (const n of names) if (!rendered.has(n)) { rendered.add(n); changed = true }
  if (changed) { version++; listeners.forEach(l => l()) }
}
export const renderedModels = () => rendered
export const coverageVersion = () => version
export const subscribeCoverage = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
