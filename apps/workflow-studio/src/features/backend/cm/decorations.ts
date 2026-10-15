/**
 * Editor decorations for the Backend view:
 * - AI-diff tint + left bar on lines added/changed by a pending draft (§4.2 step 4)
 * - red line tint for lines with errors (§4.4)
 * - secret widgets: amber wavy underline on the key + a block widget under the statement (§4.5)
 */
import { Decoration, EditorView, WidgetType, type DecorationSet } from '@codemirror/view'
import { RangeSetBuilder, StateField, type Extension, type Text } from '@codemirror/state'

const addedLine = Decoration.line({ class: 'cm-ai-added' })
const errorLine = Decoration.line({ class: 'cm-error-line' })

const baseTheme = EditorView.baseTheme({
  '.cm-ai-added': { backgroundColor: 'var(--line-added)', boxShadow: 'inset 2px 0 0 var(--success)' },
  '.cm-error-line': { backgroundColor: 'color-mix(in srgb, var(--destructive) 7%, transparent)' },
  '.cm-secret-squiggle': {
    textDecoration: 'underline wavy var(--warning)',
    textDecorationSkipInk: 'none',
    textUnderlineOffset: '3px',
  },
  '.cm-secret-widget': {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '8px',
    margin: '3px 0 4px 0',
    padding: '0 6px 0 10px',
    minHeight: '28px',
    borderRadius: '7px',
    border: '1px solid color-mix(in srgb, var(--warning) 35%, transparent)',
    backgroundColor: 'color-mix(in srgb, var(--warning) 7%, var(--card))',
    fontFamily: 'var(--font-sans)',
    fontSize: '12px',
    lineHeight: '1',
    color: 'var(--foreground)',
    whiteSpace: 'normal',
  },
  '.cm-secret-widget code': { fontFamily: 'var(--font-mono)', fontSize: '11.5px' },
  '.cm-secret-widget button': {
    height: '22px',
    padding: '0 8px',
    borderRadius: '6px',
    border: '1px solid var(--border)',
    background: 'var(--background)',
    color: 'var(--foreground)',
    font: '500 12px/1 var(--font-sans)',
    cursor: 'pointer',
  },
  '.cm-secret-widget button.cm-secret-dismiss': { border: 'none', background: 'transparent', color: 'var(--muted-foreground)', padding: '0 4px' },
})

function lineSet(doc: Text, lines: number[], deco: Decoration): DecorationSet {
  const b = new RangeSetBuilder<Decoration>()
  for (const n of [...new Set(lines)].sort((a, c) => a - c)) {
    if (n >= 1 && n <= doc.lines) {
      const l = doc.line(n)
      b.add(l.from, l.from, deco)
    }
  }
  return b.finish()
}

/** Static line tints (recreated when the inputs change; cheap). */
export function lineTints(opts: { added: number[]; errors: number[] }): Extension {
  return [
    baseTheme,
    StateField.define<DecorationSet>({
      create: (state) => {
        const a = lineSet(state.doc, opts.added, addedLine)
        const e = lineSet(state.doc, opts.errors, errorLine)
        return a.update({ add: [...iter(e)], sort: true })
      },
      update: (deco, tr) => deco.map(tr.changes),
      provide: (f) => EditorView.decorations.from(f),
    }),
  ]
}

function* iter(set: DecorationSet) {
  const c = set.iter()
  while (c.value) {
    yield c.value.range(c.from, c.to)
    c.next()
  }
}

// ── secret widgets ──────────────────────────────────────────────────────────

export type SecretIssueKind = 'missing_everywhere' | 'missing_in' | 'not_bound'
export interface SecretIssue {
  key: string
  kind: SecretIssueKind
  presentIn: string[]
  missingIn: string[]
  host?: string | null
}

export interface SecretWidgetHandlers {
  onFix: (issue: SecretIssue, anchor: HTMLElement) => void
  onDismiss: (key: string) => void
}

const SECRET_RE = /studio\.secret\(\s*['"]([A-Za-z_]\w*)['"]\s*\)/g
const VARS_RE = /\$vars\.(\w+)/g

/** Client scan (§4.5): every referenced key with its line and character range. */
export function scanSecretRefs(source: string): Array<{ key: string; line: number; from: number; to: number }> {
  const out: Array<{ key: string; line: number; from: number; to: number }> = []
  for (const re of [SECRET_RE, VARS_RE]) {
    re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(source))) {
      const keyStart = m.index + m[0].indexOf(m[1])
      const line = source.slice(0, m.index).split('\n').length
      out.push({ key: m[1], line, from: keyStart, to: keyStart + m[1].length })
    }
  }
  return out
}

class SecretWidget extends WidgetType {
  readonly issue: SecretIssue
  readonly handlers: SecretWidgetHandlers
  constructor(issue: SecretIssue, handlers: SecretWidgetHandlers) {
    super()
    this.issue = issue
    this.handlers = handlers
  }
  eq(other: SecretWidget) {
    return JSON.stringify(other.issue) === JSON.stringify(this.issue)
  }
  toDOM() {
    const wrap = document.createElement('div')
    wrap.style.paddingLeft = '4px'
    const box = document.createElement('div')
    box.className = 'cm-secret-widget'
    box.setAttribute('data-testid', `secret-widget-${this.issue.key}`)
    box.setAttribute('data-kind', this.issue.kind)
    const text = document.createElement('span')
    const key = `<code>${this.issue.key}</code>`
    let label = 'Add secret'
    if (this.issue.kind === 'missing_everywhere') text.innerHTML = `${key} is missing everywhere`
    else if (this.issue.kind === 'missing_in') {
      text.innerHTML = `${key} is set in <b>${this.issue.presentIn.join(', ')}</b>, missing in <b>${this.issue.missingIn.join(', ')}</b>`
      label = `Add to ${this.issue.missingIn[0]}`
    } else {
      text.innerHTML = `${key} is not bound to a host: Python calls are refused`
      label = this.issue.host ? `Bind to ${this.issue.host}` : 'Bind host'
    }
    const fix = document.createElement('button')
    fix.type = 'button'
    fix.textContent = label
    fix.setAttribute('data-testid', `secret-widget-fix-${this.issue.key}`)
    fix.onmousedown = (e) => e.preventDefault()
    fix.onclick = () => this.handlers.onFix(this.issue, box)
    const x = document.createElement('button')
    x.type = 'button'
    x.className = 'cm-secret-dismiss'
    x.setAttribute('aria-label', 'Hide')
    x.textContent = '×'
    x.onmousedown = (e) => e.preventDefault()
    x.onclick = () => this.handlers.onDismiss(this.issue.key)
    box.append(keyIcon(), text, fix, x)
    wrap.append(box)
    return wrap
  }
  ignoreEvent() {
    return true
  }
}

function keyIcon() {
  const s = document.createElement('span')
  s.style.color = 'var(--warning)'
  s.textContent = '⚿'
  return s
}

/** End offset of the statement containing `pos`: extends over open brackets onto following lines. */
function statementEnd(doc: Text, pos: number): number {
  const startLine = doc.lineAt(pos)
  let depth = 0
  for (let n = startLine.number; n <= doc.lines; n++) {
    const l = doc.line(n)
    const text = n === startLine.number ? l.text : l.text
    for (const ch of text) {
      if ('([{'.includes(ch)) depth++
      else if (')]}'.includes(ch)) depth--
    }
    if (depth <= 0) return l.to
  }
  return doc.length
}

export function secretWidgets(issues: SecretIssue[], handlers: SecretWidgetHandlers): Extension {
  const byKey = new Map(issues.map((i) => [i.key, i]))
  const build = (doc: Text): DecorationSet => {
    const refs = scanSecretRefs(doc.toString()).filter((r) => byKey.has(r.key))
    const ranges: Array<{ from: number; to: number; deco: Decoration }> = []
    const placed = new Set<string>()
    for (const r of refs) {
      ranges.push({ from: r.from, to: r.to, deco: Decoration.mark({ class: 'cm-secret-squiggle' }) })
      if (placed.has(r.key)) continue
      placed.add(r.key)
      const end = statementEnd(doc, r.from)
      ranges.push({
        from: end,
        to: end,
        deco: Decoration.widget({ widget: new SecretWidget(byKey.get(r.key)!, handlers), block: true, side: 1 }),
      })
    }
    ranges.sort((a, b) => a.from - b.from || a.to - b.to)
    const b = new RangeSetBuilder<Decoration>()
    for (const r of ranges) b.add(r.from, r.to, r.deco)
    return b.finish()
  }
  return [
    baseTheme,
    StateField.define<DecorationSet>({
      create: (state) => build(state.doc),
      update: (deco, tr) => (tr.docChanged ? build(tr.state.doc) : deco),
      provide: (f) => EditorView.decorations.from(f),
    }),
  ]
}
