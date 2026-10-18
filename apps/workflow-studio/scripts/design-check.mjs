#!/usr/bin/env node
/**
 * design:check — enforces DESIGN-STANDARD.md in feature and shared code.
 *
 *   npm run design:check                    # every file under src/features, src/shared, src/app
 *   npm run design:check -- src/features/data   # only these files / folders
 *   npm run design:check -- --summary       # counts per folder instead of every line
 *
 * Prints `file:line  rule  snippet` and exits 1 when anything is found. Comment lines are skipped.
 * A line can opt out with a trailing `// design-ok: <reason>` (use sparingly: generated-app colours,
 * canvas painting, third-party theme objects).
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DEFAULT_DIRS = ['src/features', 'src/shared', 'src/app']

/** Files that are allowed to break the rules (see DESIGN-STANDARD.md "Allow-list"). */
const ALLOW = [
  'src/index.css',
  'src/shared/lib/starfield.ts', // canvas painting: literal star colours
  'src/features/app-builder/sandbox/', // the generated app's own runtime and colours
  'src/app/routes/design.tsx', // the reference page shows raw tokens on purpose
  'src/shared/lib/liquid-glass.ts', // sets the refraction filter as an inline backdrop-filter
]

const PALETTE = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
const RULES = [
  { id: 'raw-palette', re: new RegExp(`\\b(?:bg|text|border|ring|fill|stroke|from|to|via|shadow|outline|divide|placeholder|accent|caret|decoration)-(?:${PALETTE})-\\d{2,3}\\b`), why: 'raw Tailwind palette colour; use a token (text-success, bg-hue-data/10 …)' },
  { id: 'white-black', re: /\b(?:bg|text|border|ring|fill|stroke)-(?:white|black)\b/, why: 'bg-white / text-black …; use a token (bg-raise, text-primary-foreground …)' },
  { id: 'hex', re: /(?<![\w&])#[0-9a-fA-F]{3,8}\b(?![\w-])/, why: 'hex colour literal; use a token' },
  { id: 'rgb', re: /\b(?:rgba?|hsla?|oklch)\(\s*\d/, why: 'rgb()/hsl() literal; use a token' },
  { id: 'box-border', re: /(?:^|[\s"'`{(])(?:border|border-[xytrbl]?-?(?:border|panel-border|input)|border-(?:border|panel-border|input)(?:\/\d+)?)(?=[\s"'`})]|$)/, why: 'border as a box edge; boxes are glass (Tile / Well); row dividers use border-b border-hairline' },
  { id: 'ring-edge', re: /\bring-1\b|\bring-foreground\/\d+/, why: 'ring as a box edge; overlays use glass-overlay, focus uses focus-visible:shadow-focus' },
  { id: 'radius', re: /\brounded-(?:2xl|3xl|4xl)\b|\brounded(?:-[trblse]{1,2})?-\[\d+(?:\.\d+)?px\]/, why: 'off-scale radius; use rounded-sheet / -panel / -well / -ctl / -chip / -kbd (or rounded-full)' },
  { id: 'gradient', re: /\bbg-(?:gradient|linear|radial|conic)-|linear-gradient\(/, why: 'gradient inside a box; only the brand mark and avatars (brand-fill) carry a gradient' },
  { id: 'eyebrow', re: /\buppercase\b/, why: 'uppercase eyebrow; labels are sentence case' },
  { id: 'raw-backdrop', re: /\bbackdrop-(?:blur|saturate|brightness)(?!-none)\b|\bbackdropFilter\b/, why: 'hand-rolled backdrop blur; cards never blur, floating chrome uses glass-chrome, overlays glass-overlay' },
  { id: 'hand-rolled-glass', re: /\bbg-\(--(?:glass|overlay|chrome)-fill\)/, why: 'glass fill by hand; use Tile / the tile, glass, glass-chrome, glass-strip or glass-overlay recipe' },
  { id: 'hand-rolled-list', re: /\bdivide-y\s+divide-hairline\b/, why: 'list built by hand; use WellList + WellRow (one Well, hairline rows)' },
  { id: 'legacy-surface', re: /\bbg-(?:card|background|popover|canvas)\b(?!\/)/, why: 'solid legacy surface; pages are transparent (the sky shows), boxes are Tile / Well, overlays glass-overlay' },
]

function walk(p, out) {
  const st = fs.statSync(p)
  if (st.isDirectory()) {
    for (const f of fs.readdirSync(p)) if (f !== 'node_modules' && f !== '__tests__') walk(path.join(p, f), out)
  } else if (/\.(tsx?|css)$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  return out
}

const argv = process.argv.slice(2)
const summary = argv.includes('--summary')
const targets = argv.filter((a) => !a.startsWith('--'))
const files = (targets.length ? targets : DEFAULT_DIRS).flatMap((t) => walk(path.resolve(ROOT, t), []))

const hits = []
for (const abs of files) {
  const rel = path.relative(ROOT, abs)
  if (ALLOW.some((a) => rel === a || (a.endsWith('/') && rel.startsWith(a)))) continue
  const lines = fs.readFileSync(abs, 'utf8').split('\n')
  let inBlock = false
  lines.forEach((line, i) => {
    const t = line.trim()
    if (inBlock) {
      if (t.includes('*/')) inBlock = false
      return
    }
    if (t.startsWith('/*') && !t.includes('*/')) {
      inBlock = true
      return
    }
    if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return
    if (/\/\/ design-ok\b/.test(line)) return
    for (const r of RULES) {
      const m = r.re.exec(line)
      if (m) hits.push({ rel, line: i + 1, rule: r.id, why: r.why, snip: t.slice(Math.max(0, m.index - (line.length - t.length) - 30), m.index - (line.length - t.length) + 60) })
    }
  })
}

if (summary) {
  const by = new Map()
  for (const h of hits) {
    const k = h.rel.split('/').slice(0, 3).join('/')
    by.set(k, (by.get(k) ?? 0) + 1)
  }
  for (const [k, n] of [...by].sort((a, b) => b[1] - a[1])) console.log(String(n).padStart(5), k)
  console.log(String(hits.length).padStart(5), 'total')
} else {
  for (const h of hits) console.log(`${h.rel}:${h.line}  ${h.rule}  ${h.snip}`)
  if (hits.length) {
    console.log(`\n${hits.length} design-check finding(s). Rules:`)
    for (const r of RULES) if (hits.some((h) => h.rule === r.id)) console.log(`  ${r.id}: ${r.why}`)
  } else console.log('design:check clean')
}
process.exit(hits.length ? 1 : 0)
