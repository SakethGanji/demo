// Component registry of the UI kit (and optionally an app's own files): the same extractor the Studio's props panel uses
// (workflow-studio/src/features/app-builder/visual/registry/extract.ts, loaded by Node's TypeScript type stripping), so
// the builder prompt and the panel can never disagree about a component's props.
//
// stdin: {kit_dir, parser, files?: [{path, content}], extractor?}
//   kit_dir    the app_kit folder (its files are keyed kit/<name>, as an app carries them)
//   parser     path of @babel/parser's lib/index.js (editability_lint.py's _parser() finds it)
//   files      an app's own files: their components are added (exported or not), each with `file` set to its path
//   extractor  override for extract.ts's path
// stdout: {components: [{name, file, exported, category?, description?, example?, children, props: [PropSchema]}], ms}
// Kit components are listed only when exported. Usage: `node registry.mjs < input.json`, or
// `node registry.mjs --kit <dir> --parser <index.js>` for the kit alone.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_EXTRACTOR = path.resolve(HERE, '../../../../workflow-studio/src/features/app-builder/visual/registry/extract.ts')

function readInput() {
  const argv = process.argv.slice(2)
  const flag = (name) => {
    const i = argv.indexOf(name)
    return i >= 0 ? argv[i + 1] : undefined
  }
  if (argv.length) return { kit_dir: flag('--kit'), parser: flag('--parser'), extractor: flag('--extractor') }
  return JSON.parse(fs.readFileSync(0, 'utf8'))
}

const input = readInput()
const kitDir = input.kit_dir ?? path.resolve(HERE, '../app_kit')
const parserPath = input.parser ?? path.resolve(HERE, '../../../../workflow-studio/node_modules/@babel/parser/lib/index.js')
const parser = createRequire(import.meta.url)(parserPath)
const { extractComponents } = await import(pathToFileURL(input.extractor ?? DEFAULT_EXTRACTOR).href)

const parse = (src) => parser.parse(src, { sourceType: 'module', plugins: ['typescript', 'jsx'], errorRecovery: true })

const files = {}
for (const f of fs.readdirSync(kitDir)) if (/\.tsx?$/.test(f)) files[`kit/${f}`] = fs.readFileSync(path.join(kitDir, f), 'utf8')
const own = []
for (const f of input.files ?? []) {
  if (!f || typeof f.path !== 'string' || typeof f.content !== 'string' || f.path.startsWith('kit/')) continue
  files[f.path] = f.content
  if (/\.(t|j)sx$/.test(f.path)) own.push(f.path)
}

const t0 = performance.now()
const kit = extractComponents(files, parse, Object.keys(files).filter((p) => p.startsWith('kit/'))).filter((c) => c.exported)
const app = own.length ? extractComponents(files, parse, own) : []
const strip = ({ start, end, ...c }) => c
process.stdout.write(JSON.stringify({ components: [...kit, ...app].map(strip), ms: Math.round(performance.now() - t0) }))
