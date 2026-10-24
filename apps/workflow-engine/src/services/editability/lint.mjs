// Editability lint of a generated app: how much of its JSX the Studio's visual editor can edit, and the top shape-rule violations.
// stdin: {files: [{path, content}], kit_dir, parser, max_violations}; stdout: the report as JSON (see editability_lint.py).
// The element-level definition mirrors the visual-edit study's analyze.mjs: a JSX element is editable when it is a kit component whose
// config props are all literal / static-ref / bound / JSX slots (behaviour props never block, a spread does). The denominator is every
// JSX element of the app's own files (score_strict), or every element but host-table markup, which collapses into DataTable config (score).
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const parser = createRequire(import.meta.url)(input.parser)
const MAX = input.max_violations ?? 20

const KIT = new Set()
for (const f of fs.readdirSync(input.kit_dir)) {
  if (!/\.tsx?$/.test(f)) continue
  for (const m of fs.readFileSync(path.join(input.kit_dir, f), 'utf8').matchAll(/^export (?:function|const) ([A-Z]\w*)/gm)) KIT.add(m[1])
}

const BEHAVIOR = /^(on[A-Z]\w*|loading|disabled|open|value|checked|activeKey|selectedId|selected|data|rows|items|status|frame|steps|results|before|after|code|values|pollKey|startedAt|jobId|record|row|id|reloadKey|ref|live|count|patch|lines|checks|src|url|elapsed|progress|events|frames|total|page|offset|filters|initial|defaultValue|href)$/
const LAYOUT_TAGS = new Set(['div', 'section', 'header', 'footer', 'main', 'nav', 'aside', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'strong', 'em', 'small', 'label', 'b', 'i', 'code', 'pre', 'hr', 'br'])
const CONTROL_TAGS = new Set(['button', 'input', 'select', 'option', 'textarea', 'a', 'form', 'details', 'summary'])
const TABLE_TAGS = new Set(['table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'colgroup', 'col', 'caption'])
const SKIP_KEYS = new Set(['loc', 'start', 'end', 'extra', 'leadingComments', 'trailingComments', 'innerComments', 'range', 'errors', 'tokens', 'comments'])
const NEUTRAL = /\b(?:text|bg|border|ring|divide|from|to|via|fill|stroke|placeholder|outline|decoration|shadow)-(?:slate|gray|zinc|neutral|stone)-\d{2,3}(?:\/\d+)?\b/g
const BADGE_COMPS = new Set(['Tag', 'StatusBadge', 'StateBadge'])
const RULE_ORDER = ['R3-column-render', 'R3-columns-in-component', 'R3-columns-inline', 'R4-raw-select', 'R4-raw-tabs', 'R5-imperative-screens', 'R2-config-expr', 'R2-config-lambda', 'R8-slate-gray']

const parse = (src) => parser.parse(src, { sourceType: 'module', plugins: ['typescript', 'jsx'], errorRecovery: true })

function isLiteralNode(n) {
  if (!n) return false
  switch (n.type) {
    case 'StringLiteral': case 'NumericLiteral': case 'BooleanLiteral': case 'NullLiteral': return true
    case 'TemplateLiteral': return n.expressions.length === 0
    case 'UnaryExpression': return (n.operator === '-' || n.operator === '+') && n.argument.type === 'NumericLiteral'
    case 'ArrayExpression': return n.elements.every((e) => e && isLiteralNode(e))
    case 'ObjectExpression': return n.properties.every((p) => p.type === 'ObjectProperty' && !p.computed && isLiteralNode(p.value))
    case 'TSAsExpression': case 'TSSatisfiesExpression': case 'TSNonNullExpression': return isLiteralNode(n.expression)
    case 'Identifier': return n.name === 'undefined'
    default: return false
  }
}

// every AST node under `n` (children first-in-source order), with its ancestors; `fn` returns false to skip a subtree
function walk(n, fn, anc = []) {
  if (!n || typeof n !== 'object') return
  if (fn(n, anc) === false) return
  anc.push(n)
  for (const k of Object.keys(n)) {
    if (SKIP_KEYS.has(k)) continue
    const v = n[k]
    if (Array.isArray(v)) { for (const x of v) if (x && typeof x.type === 'string') walk(x, fn, anc) }
    else if (v && typeof v.type === 'string') walk(v, fn, anc)
  }
  anc.pop()
}
const some = (n, test) => { let f = false; walk(n, (x) => { if (f) return false; if (test(x)) { f = true; return false } }); return f }
const isFn = (x) => x.type === 'ArrowFunctionExpression' || x.type === 'FunctionExpression'
const hasFunction = (n) => some(n, isFn)
const unwrap = (n) => (n && (n.type === 'TSAsExpression' || n.type === 'TSSatisfiesExpression' || n.type === 'TSNonNullExpression') ? unwrap(n.expression) : n)
const propKey = (p) => (p.key?.type === 'Identifier' ? p.key.name : p.key?.type === 'StringLiteral' ? p.key.value : null)
const jsxName = (op) => (op.name.type === 'JSXIdentifier' ? op.name.name : op.name.type === 'JSXMemberExpression' ? `${op.name.object.name}.${op.name.property.name}` : 'ns')
const attrName = (a) => (typeof a.name.name === 'string' ? a.name.name : a.name.name.name)
const lineOf = (n) => n.loc?.start.line ?? 0

/** A column `render` that only shows its own field: `(r) => r.status`, `(r) => <Tag tone={T[r.status]}>{r.status}</Tag>`, a date of r.x.
 * Returns the format it maps to ('text' | 'badge' | 'date' | 'money') or null for a genuine multi-field cell. */
function trivialRender(fn, key) {
  if (!fn || !isFn(fn) || fn.params.length !== 1 || fn.params[0].type !== 'Identifier' || !key) return null
  const p = fn.params[0].name
  let other = false, own = false, badge = false, date = false, money = false
  walk(fn.body, (x, anc) => {
    if (other) return false
    if (x.type === 'Identifier' && x.name === p) {
      const parent = anc[anc.length - 1]
      const isObj = parent && (parent.type === 'MemberExpression' || parent.type === 'OptionalMemberExpression') && parent.object === x
      const name = isObj ? (parent.computed ? (parent.property.type === 'StringLiteral' ? parent.property.value : null) : parent.property.name) : null
      if (name === key) own = true
      else other = true
    }
    if (x.type === 'JSXOpeningElement' && BADGE_COMPS.has(jsxName(x))) badge = true
    if (x.type === 'Identifier' && /^(Date|timeAgo|fmtDate|formatDate|toLocaleDateString|toLocaleTimeString)$/.test(x.name)) date = true
    if (x.type === 'Identifier' && /^(toFixed|currency|fmtMoney|formatMoney)$/.test(x.name)) money = true
    if (x.type === 'StringLiteral' && /^[$€£]$/.test(x.value)) money = true
  })
  if (other || !own) return null
  return badge ? 'badge' : money ? 'money' : date ? 'date' : 'text'
}

const results = []
const files = (input.files || []).filter((f) => f && typeof f.path === 'string' && typeof f.content === 'string')
  .map((f) => ({ path: f.path.replace(/^\.\//, ''), content: f.content }))
  .filter((f) => !/^(src\/)?kit\//.test(f.path) && !/(^|\/)api\/client\.ts$/.test(f.path) && /\.(tsx?|jsx?)$/.test(f.path))

// pass 1: parse, top-level names and literal consts (exported ones by module path, so an imported COLUMNS counts as static)
const parsed = new Map()
const exportedLits = new Map()
for (const f of files) {
  let ast
  try { ast = parse(f.content) } catch { continue }
  const lits = new Set(), exp = new Set(), top = new Set()
  for (const st of ast.program.body) {
    const decl = st.type === 'ExportNamedDeclaration' || st.type === 'ExportDefaultDeclaration' ? st.declaration : st
    if (!decl) continue
    if (decl.type === 'VariableDeclaration') {
      for (const d of decl.declarations) {
        if (d.id.type !== 'Identifier') continue
        top.add(d.id.name)
        if (decl.kind === 'const' && isLiteralNode(d.init)) { lits.add(d.id.name); if (st !== decl) exp.add(d.id.name) }
      }
    } else if (decl.id?.name) top.add(decl.id.name)
    if (st.type === 'ImportDeclaration') for (const s of st.specifiers) top.add(s.local.name)
  }
  exportedLits.set(f.path.replace(/\.(tsx?|jsx?)$/, ''), exp)
  parsed.set(f.path, { ast, lits, top, src: f.content })
}

const totals = { elements: 0, host_table: 0, kit_nodes: 0, editable: 0, column_renders: 0, column_renders_trivial: 0 }
const violations = []
const perFile = []
const v = (file, node, rule, message, fix) => violations.push({ file, line: lineOf(node), rule, message, fix })

for (const [rel, { ast, lits, top }] of parsed) {
  const staticNames = new Set(lits)
  for (const st of ast.program.body) if (st.type === 'ImportDeclaration' && st.source.value.startsWith('.')) {
    const target = path.posix.normalize(path.posix.join(path.posix.dirname(rel), st.source.value))
    const exp = exportedLits.get(target) || exportedLits.get(target + '/index')
    if (exp) for (const s of st.specifiers) if (s.type === 'ImportSpecifier' && exp.has(s.imported.name)) staticNames.add(s.local.name)
  }
  const s = { file: rel, elements: 0, host_table: 0, kit_nodes: 0, editable: 0 }
  const screensIds = new Set()
  walk(ast, (n, anc) => {
    // R8: neutral palette classes in any string of the file
    if (n.type === 'StringLiteral' || n.type === 'TemplateElement') {
      const text = n.type === 'StringLiteral' ? n.value : n.value.raw
      const hits = text.match(NEUTRAL)
      if (hits) v(rel, n, 'R8-slate-gray', `hard-coded neutral colour ${[...new Set(hits)].slice(0, 3).join(' ')}`, 'use theme token utilities (text-muted-foreground, bg-muted, bg-card, border-border, text-foreground) or kit tones')
    }
    // R3: column objects ({key, header, render})
    if (n.type === 'ObjectExpression') {
      const props = Object.fromEntries(n.properties.filter((p) => p.type === 'ObjectProperty' || p.type === 'ObjectMethod').map((p) => [propKey(p), p]))
      if (props.header && props.render) {
        totals.column_renders++
        const keyNode = props.key?.value
        const key = keyNode?.type === 'StringLiteral' ? keyNode.value : null
        const fn = props.render.type === 'ObjectMethod' ? null : props.render.value
        const fmt = trivialRender(fn, key)
        if (fmt) {
          totals.column_renders_trivial++
          v(rel, props.render, 'R3-column-render', `column '${key}' has a render that only shows its own field`,
            fmt === 'text' ? `drop render (or use format: 'text')` : fmt === 'badge' ? `use format: 'badge' with toneMap: <module-level as const map>` : `use format: '${fmt === 'date' ? 'date' : 'money'}' (or 'datetime' / 'ago')`)
        }
      }
    }
    // R5: screens built imperatively
    if (n.type === 'CallExpression' && n.callee.type === 'MemberExpression' && !n.callee.computed && ['push', 'unshift', 'splice'].includes(n.callee.property.name)
      && n.callee.object.type === 'Identifier' && /screens?$|^nav(Items)?$|^menu(Items)?$/i.test(n.callee.object.name)) {
      v(rel, n, 'R5-imperative-screens', `${n.callee.object.name}.${n.callee.property.name}(...) builds the navigation in code`, "list every screen in a literal screens array; give role-only screens roles: ['role'] instead of pushing them under an if")
    }
    if (n.type !== 'JSXElement') return
    const op = n.openingElement
    const name = jsxName(op)
    s.elements++
    let kind
    if (/^[a-z]/.test(name)) kind = LAYOUT_TAGS.has(name) ? 'host-layout' : CONTROL_TAGS.has(name) ? 'host-control' : TABLE_TAGS.has(name) ? 'host-table' : 'host-other'
    else if (name === 'React.Fragment' || name === 'Fragment') kind = 'fragment'
    else kind = KIT.has(name) ? 'kit' : 'local'
    if (kind === 'host-table') s.host_table++
    if (name === 'select') v(rel, n, 'R4-raw-select', 'raw <select>', "use the kit Select({label?, value, onChange, options})")
    if (name === 'button') {
      const at = Object.fromEntries(op.attributes.filter((a) => a.type === 'JSXAttribute').map((a) => [attrName(a), a]))
      const role = at.role?.value?.type === 'StringLiteral' ? at.role.value.value : null
      const cls = at.className?.value?.type === 'JSXExpressionContainer' ? at.className.value.expression : null
      const tabby = role === 'tab' || !!at['aria-selected'] || (cls && some(cls, (x) => x.type === 'BinaryExpression' && /^[!=]==?$/.test(x.operator)
        && [x.left, x.right].some((y) => (y.type === 'Identifier' && /tab|view|mode|section|filter/i.test(y.name)) || (y.type === 'MemberExpression' && !y.computed && /tab/i.test(y.object.name || '')))))
      if (tabby) v(rel, n, 'R4-raw-tabs', 'hand-rolled tab button with a conditional class', 'use the kit Tabs tabs={[{id, label}]} value={tab} onChange={setTab}')
    }
    if (kind !== 'kit') return
    s.kit_nodes++
    const blockers = new Set()
    for (const a of op.attributes) {
      if (a.type === 'JSXSpreadAttribute') { blockers.add('spread'); continue }
      const pname = attrName(a)
      if (pname === 'key' || pname === 'className' || pname === 'style') continue
      const val = a.value
      const isHandler = /^on[A-Z]/.test(pname)
      let cls
      if (val === null || val.type === 'StringLiteral') cls = 'literal'
      else if (val.type === 'JSXExpressionContainer') {
        const e = val.expression
        if (isLiteralNode(e)) cls = 'literal'
        else if (e.type === 'Identifier' && staticNames.has(e.name)) cls = 'staticRef'
        else if (isFn(e)) cls = isHandler ? 'handler' : 'lambda'
        else if (isHandler && (e.type === 'Identifier' || e.type === 'MemberExpression')) cls = 'handler'
        else if (e.type === 'Identifier' || e.type === 'MemberExpression' || e.type === 'OptionalMemberExpression') cls = 'binding'
        else if (e.type === 'JSXElement' || e.type === 'JSXFragment') cls = 'jsxSlot'
        else if (hasFunction(e)) cls = 'lambda'
        else if (e.type === 'TemplateLiteral') cls = 'template'
        else cls = 'expr'
        if (pname === 'columns' && e.type === 'Identifier' && !top.has(e.name)) v(rel, a, 'R3-columns-in-component', `columns '${e.name}' are built inside the component`, 'move the columns to a module-level const (COLUMNS: Column<Row>[]) or inline them in the JSX')
        if (pname === 'screens' && unwrap(e)?.type === 'ArrayExpression' && unwrap(e).elements.some((x) => x && (x.type === 'SpreadElement' || x.type === 'ConditionalExpression' || x.type === 'LogicalExpression')))
          v(rel, a, 'R5-imperative-screens', 'screens are added conditionally', "list every screen; give role-only screens roles: ['role']")
        if (pname === 'screens' && e.type === 'Identifier') screensIds.add(e.name)
      } else cls = 'jsxSlot'
      if (BEHAVIOR.test(pname)) continue
      if (cls === 'lambda' || cls === 'expr' || cls === 'template') blockers.add(cls)
      if (cls === 'expr' || cls === 'template') {
        const e = unwrap(val.expression)
        const toneish = /tone|variant|color|kind|width/i.test(pname)
        v(rel, a, 'R2-config-expr', `${name}.${pname} is an inline ${e.type === 'ConditionalExpression' ? 'ternary' : cls === 'template' ? 'template' : 'expression'}`,
          toneish ? `use a module-level map: const ${pname.toUpperCase()}_BY_X = {...} as const, then ${pname}={${pname.toUpperCase()}_BY_X[value]}` : `compute it once above the return (const ${pname} = ...) or make it a literal`)
      } else if (cls === 'lambda' && pname !== 'columns' && pname !== 'screens') {   // a literal screens array renders through functions by design
        v(rel, a, 'R2-config-lambda', `${name}.${pname} is an inline function`, `move it to a named module-level function (const ${pname} = (r: Row) => ...)`)
      } else if (cls === 'lambda' && pname === 'columns') {
        v(rel, a, 'R3-columns-inline', `${name}.columns are inline with render functions`, 'move the columns to a module-level const; single-field cells use format instead of render')
      }
    }
    if (blockers.size === 0) s.editable++
  })
  // screens pushed onto an array AppShell is given under another name
  if (screensIds.size) walk(ast, (n) => {
    if (n.type === 'CallExpression' && n.callee.type === 'MemberExpression' && n.callee.object.type === 'Identifier' && screensIds.has(n.callee.object.name)
      && !/screens?$/i.test(n.callee.object.name) && ['push', 'unshift', 'splice'].includes(n.callee.property?.name))
      v(rel, n, 'R5-imperative-screens', `${n.callee.object.name}.${n.callee.property.name}(...) builds the navigation in code`, "list every screen in a literal screens array; give role-only screens roles: ['role']")
  })
  for (const k of ['elements', 'host_table', 'kit_nodes', 'editable']) totals[k] += s[k]
  const eligible = s.elements - s.host_table
  perFile.push({ ...s, eligible, score: eligible ? +(s.editable / eligible).toFixed(3) : null })
}

const eligible = totals.elements - totals.host_table
const counts = {}
for (const x of violations) counts[x.rule] = (counts[x.rule] || 0) + 1
// one per (file, line, rule); the high-value mechanical rules first
const seen = new Set()
const top = violations.filter((x) => { const k = `${x.file}:${x.line}:${x.rule}`; if (seen.has(k)) return false; seen.add(k); return true })
  .sort((a, b) => RULE_ORDER.indexOf(a.rule) - RULE_ORDER.indexOf(b.rule) || a.file.localeCompare(b.file) || a.line - b.line)
process.stdout.write(JSON.stringify({
  available: true,
  score: eligible ? +(totals.editable / eligible).toFixed(3) : null,
  score_strict: totals.elements ? +(totals.editable / totals.elements).toFixed(3) : null,
  kit_nodes: totals.kit_nodes, editable: totals.editable, eligible, elements: totals.elements, host_table: totals.host_table,
  column_renders: totals.column_renders, column_renders_trivial: totals.column_renders_trivial,
  files: perFile.length, unparsed: files.length - parsed.size,
  violation_counts: counts, violations: top.slice(0, MAX), per_file: perFile,
}))
