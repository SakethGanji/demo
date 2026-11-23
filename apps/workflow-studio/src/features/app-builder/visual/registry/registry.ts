/**
 * The component registry of one app, derived from its files on every change: the kit (`kit/*`, copied into every app)
 * plus the app's own components (anything PascalCase that returns JSX, typically in components/ or lib/). Nothing here
 * is stored; it is recomputed (memoized per file map) whenever the source changes, so it cannot drift.
 */
import { extractComponents, type ComponentSchema } from './extract'
import { importsOf, indexElements, isKitImport, parseSource, tryParse, type FileMap } from '../source'

export type { ComponentSchema, PropSchema, Control } from './extract'

export interface Registry {
  components: ComponentSchema[]
  /** `file#Name` → schema */
  byKey: Map<string, ComponentSchema>
}

export function isKitFile(path: string): boolean {
  return path.startsWith('kit/') || path.includes('/kit/')
}

const memo = new WeakMap<FileMap, Registry>()
let last: { entries: [string, string][]; reg: Registry } | null = null

/** The registry of a file map (memoized by identity, then by content). */
export function registryFor(files: FileMap): Registry {
  const hit = memo.get(files)
  if (hit) return hit
  const entries = Object.entries(files).filter(([p]) => /\.(t|j)sx?$/.test(p))
  let reg: Registry
  if (last && last.entries.length === entries.length && last.entries.every(([p, c], i) => entries[i][0] === p && entries[i][1] === c)) reg = last.reg
  else {
    const all = extractComponents(Object.fromEntries(entries), parseSource)
    // kit internals (unexported helpers) are not components the app can use
    const components = all.filter((c) => c.exported || !isKitFile(c.file))
    reg = { components, byKey: new Map(components.map((c) => [`${c.file}#${c.name}`, c])) }
    last = { entries, reg }
  }
  memo.set(files, reg)
  return reg
}

function dirname(p: string): string {
  const i = p.lastIndexOf('/')
  return i < 0 ? '' : p.slice(0, i)
}

function joinPath(base: string, rel: string): string {
  const out: string[] = []
  for (const seg of `${base}/${rel}`.split('/')) {
    if (seg === '..') out.pop()
    else if (seg && seg !== '.') out.push(seg)
  }
  return out.join('/')
}

function resolveModule(files: FileMap, from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null
  const p = joinPath(dirname(from), spec)
  for (const c of [p, `${p}.ts`, `${p}.tsx`, `${p}/index.ts`, `${p}/index.tsx`]) if (c in files) return c
  return null
}

/** The component `name` exported by (or re-exported through) `file`. */
function exportedFrom(files: FileMap, reg: Registry, file: string, name: string, seen = new Set<string>()): ComponentSchema | null {
  if (seen.has(file)) return null
  seen.add(file)
  const own = reg.byKey.get(`${file}#${name}`)
  if (own) return own
  const ast = files[file] == null ? null : tryParse(files[file])
  if (!ast) return null
  for (const s of ast.program.body) {
    if (s.type === 'ExportAllDeclaration') {
      const t = resolveModule(files, file, s.source.value)
      const hit = t && exportedFrom(files, reg, t, name, seen)
      if (hit) return hit
    } else if (s.type === 'ExportNamedDeclaration' && s.source) {
      for (const sp of s.specifiers) {
        if (sp.type !== 'ExportSpecifier') continue
        const exported = sp.exported.type === 'Identifier' ? sp.exported.name : sp.exported.value
        if (exported !== name) continue
        const t = resolveModule(files, file, s.source.value)
        const hit = t && exportedFrom(files, reg, t, sp.local.name, new Set(seen))
        if (hit) return hit
      }
    } else if (s.type === 'ExportNamedDeclaration' && !s.declaration) {
      // `export { Local as Name }` of a local component
      for (const sp of s.specifiers) {
        if (sp.type !== 'ExportSpecifier') continue
        const exported = sp.exported.type === 'Identifier' ? sp.exported.name : sp.exported.value
        if (exported === name) return reg.byKey.get(`${file}#${sp.local.name}`) ?? null
      }
    } else if (s.type === 'ExportDefaultDeclaration' && name === 'default' && s.declaration.type === 'Identifier') {
      return reg.byKey.get(`${file}#${s.declaration.name}`) ?? null
    }
  }
  if (name === 'default') return reg.components.find((c) => c.file === file && c.exported) ?? null
  return null
}

/** The schema of the component a JSX element named `name` in `file` renders, or null for DOM elements and unknowns. */
export function componentFor(files: FileMap, file: string, name: string): ComponentSchema | null {
  if (!/^[A-Z]/.test(name) || name.includes('.')) return null
  const reg = registryFor(files)
  const local = reg.byKey.get(`${file}#${name}`)
  if (local) return local
  const src = files[file]
  const ast = src == null ? null : tryParse(src)
  const imp = ast && importsOf(ast).get(name)
  if (!imp) return null
  const target = resolveModule(files, file, imp.source)
  return target ? exportedFrom(files, reg, target, imp.imported) : null
}

/** Whether an element named `name` in `file` comes from the kit. */
export function isKitElement(files: FileMap, file: string, name: string): boolean {
  const src = files[file]
  const ast = src == null ? null : tryParse(src)
  const imp = ast && importsOf(ast).get(name.split('.')[0])
  return !!imp && isKitImport(imp.source)
}

export interface Usage {
  file: string
  key: string
}

const usageMemo = new WeakMap<FileMap, Map<string, Usage[]>>()

/** Every place in the app's own files that renders `comp` (a static count over the import graph). */
export function usagesOf(files: FileMap, comp: ComponentSchema): Usage[] {
  let byComp = usageMemo.get(files)
  if (!byComp) {
    byComp = new Map()
    usageMemo.set(files, byComp)
  }
  const id = `${comp.file}#${comp.name}`
  const hit = byComp.get(id)
  if (hit) return hit
  const out: Usage[] = []
  for (const [file, src] of Object.entries(files)) {
    if (isKitFile(file) || !/\.(t|j)sx$/.test(file) || !src.includes(comp.name)) continue
    const ast = tryParse(src)
    if (!ast) continue
    for (const e of indexElements(ast)) {
      if (e.name !== comp.name) continue
      const c = componentFor(files, file, e.name)
      if (c && c.file === comp.file && c.name === comp.name) out.push({ file, key: e.key })
    }
  }
  byComp.set(id, out)
  return out
}

/** The app-defined component whose body contains the element `key` of `file` (the key starts with its top-level name). */
export function enclosingComponent(files: FileMap, file: string, key: string): ComponentSchema | null {
  if (isKitFile(file)) return null
  const top = key.slice(0, key.indexOf('/'))
  return registryFor(files).byKey.get(`${file}#${top}`) ?? null
}

/** The outermost JSX element of a component's body: where "enter definition" lands. */
export function definitionRoot(files: FileMap, comp: ComponentSchema): { file: string; key: string } | null {
  const src = files[comp.file]
  if (src == null) return null
  const el = indexElements(parseSource(src)).find((e) => e.key.startsWith(`${comp.name}/`))
  return el ? { file: comp.file, key: el.key } : null
}

/**
 * A component worth treating as a unit (instance vs definition): used more than once, or living in a shared folder.
 * Screens (views/, pages/, App.tsx, `@category page`) are not: clicking inside a screen edits the screen.
 */
export function isReusable(files: FileMap, comp: ComponentSchema): boolean {
  if (isKitFile(comp.file) || comp.category === 'page' || /(^|\/)(views|pages|screens)\//.test(comp.file) && usagesOf(files, comp).length < 2) return false
  if (/(^|\/)App\.tsx$/.test(comp.file)) return false
  return usagesOf(files, comp).length >= 2 || /(^|\/)(components|lib|ui|widgets|shared)\//.test(comp.file)
}
