// Publish-time Tailwind v4 compile (app_bundler.py). The same compiler the preview runs in the browser (@tailwindcss/browser),
// so a published app gets exactly the utilities, theme tokens (@theme) and variants (@custom-variant) it had in the preview.
// stdin: {css, contents: [{content, extension}], node_modules}; stdout: the minified CSS.
// Safety: app CSS never loads code. Every @plugin / @config resolves through customJsResolver, which refuses; @import may only
// reach Tailwind's own stylesheets (the app's relative imports were already inlined by esbuild); class candidates come only
// from the file contents passed in, never from @source globs on disk.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const req = createRequire(path.join(input.node_modules, 'noop.js'))
const { compile, optimize } = req('@tailwindcss/node')
const { Scanner } = req('@tailwindcss/oxide')
const twDir = path.dirname(req.resolve('tailwindcss/package.json'))

const STYLES = { tailwindcss: 'index.css', 'tailwindcss/index.css': 'index.css', 'tailwindcss/preflight': 'preflight.css', 'tailwindcss/preflight.css': 'preflight.css',
  'tailwindcss/theme': 'theme.css', 'tailwindcss/theme.css': 'theme.css', 'tailwindcss/utilities': 'utilities.css', 'tailwindcss/utilities.css': 'utilities.css' }

async function cssResolver(id, base) {
  if (STYLES[id]) return path.join(twDir, STYLES[id])
  const inside = path.resolve(base, id)
  if (id.startsWith('.') && inside.startsWith(twDir + path.sep)) return inside
  throw new Error(`@import "${id}" is not allowed in app CSS (only Tailwind's own stylesheets)`)
}
async function jsResolver(id) {
  throw new Error(`"${id}": @plugin and @config are not allowed in app CSS`)
}

try {
  const compiler = await compile(input.css, { base: twDir, onDependency() {}, customCssResolver: cssResolver, customJsResolver: jsResolver })
  const candidates = new Scanner({}).scanFiles(input.contents)
  const out = compiler.build(candidates)
  process.stdout.write(optimize(out, { minify: true }).code)
} catch (e) {
  process.stderr.write(String(e && e.message || e))
  process.exit(1)
}
