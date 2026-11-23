/**
 * SDK autocomplete (UI-DESIGN §4.6). Every source reads the engine's single SDK reference
 * (`/api/sdk/reference`) and node catalogue (`/api/sdk/functions`).
 */
import { autocompletion, snippetCompletion, type Completion, type CompletionContext, type CompletionResult } from '@codemirror/autocomplete'
import type { Extension } from '@codemirror/state'
import type { SdkFunction, SdkReference } from '@/shared/api/types'

export interface CompletionData {
  functions: SdkFunction[]
  reference: SdkReference | null
  tables: string[]
  /** key → environments where it is set; `envs` = all project environments */
  secrets: Record<string, string[]>
  envs: string[]
}

interface PyParam {
  name: string
  type?: string
  default?: unknown
  required?: boolean
  enum?: unknown[] | null
  description?: string | null
}

type NodeCompletion = Completion & { nodeType?: string }

function pyParams(f: SdkFunction): PyParam[] {
  const py = (f as { python?: { params?: PyParam[] } }).python
  if (py?.params?.length) return py.params
  const props = ((f.parameters as { properties?: Record<string, { enum?: unknown[]; default?: unknown }> }).properties ?? {}) as Record<
    string,
    { enum?: unknown[]; default?: unknown }
  >
  const req = new Set(((f.parameters as { required?: string[] }).required ?? []) as string[])
  return Object.entries(props).map(([k, v]) => ({
    name: f.python_names?.[k] ?? k,
    required: req.has(k),
    enum: v.enum ?? null,
    default: v.default,
  }))
}

/** `(*, method, url, headers, body, …)`: keyword-only, first four names. */
export function signatureDetail(f: SdkFunction): string {
  const names = pyParams(f).map((p) => p.name)
  return `(*, ${names.slice(0, 4).join(', ')}${names.length > 4 ? ', …' : ''})`
}

function nodeSnippet(f: SdkFunction): string {
  const req = pyParams(f).filter((p) => p.required)
  if (!req.length) return `${f.function}(\${})`
  return `${f.function}(${req
    .map((p) => {
      const def = p.default != null ? JSON.stringify(p.default) : p.type === 'str' ? '""' : ''
      return `${p.name}=\${${def.replace(/[{}$]/g, '')}}`
    })
    .join(', ')})`
}

function returnsText(f: SdkFunction): string {
  const r = f.returns as { type?: string; items?: { type?: string } } | undefined
  return r?.type === 'array' ? 'returns list[dict]' : r?.type ? `returns ${r.type}` : 'returns list[dict]'
}

const TABLE_METHODS = ['insert', 'get', 'list', 'update', 'delete', 'upsert']
const DECORATORS = ['get', 'post', 'put', 'patch', 'delete']

function refEntry(data: CompletionData, name: string) {
  return data.reference?.entries.find((e) => e.name === name)
}

export function studioCompletionSource(data: CompletionData) {
  return (ctx: CompletionContext): CompletionResult | null => {
    const line = ctx.state.doc.lineAt(ctx.pos)
    const before = line.text.slice(0, ctx.pos - line.from)

    // 6. @api.
    let m = /@api\.(\w*)$/.exec(before)
    if (m) {
      return {
        from: ctx.pos - m[1].length,
        options: DECORATORS.map((d) => {
          const e = refEntry(data, `api.${d}`)
          return snippetCompletion(`${d}("\${/path}")`, {
            label: d,
            detail: '(path, *, auth="none", name=None)',
            type: 'function',
            info: e?.doc ?? `Declare a ${d.toUpperCase()} route`,
          })
        }),
      }
    }

    // 5. studio.secret("
    m = /studio\.secret\(\s*['"]([A-Za-z_0-9]*)$/.exec(before)
    if (m) {
      return {
        from: ctx.pos - m[1].length,
        options: Object.keys(data.secrets).map((k) => {
          const set = data.secrets[k]
          const missing = data.envs.filter((e) => !set.includes(e))
          return {
            label: k,
            type: 'constant',
            detail: missing.length ? `missing in ${missing.join(', ')}` : `set in ${set.join(', ')}`,
          }
        }),
        validFor: /^\w*$/,
      }
    }

    // 4a. studio.db.table("
    m = /studio\.db\.table\(\s*['"]([\w-]*)$/.exec(before)
    if (m) {
      return {
        from: ctx.pos - m[1].length,
        options: data.tables.map((t) => ({ label: t, type: 'variable', detail: 'table' })),
        validFor: /^[\w-]*$/,
      }
    }

    // 4b. .table("x"). or a variable bound to a table
    m = /(?:studio\.db\.table\(\s*['"][\w-]+['"]\s*\)|\b(\w+))\.(\w*)$/.exec(before)
    if (m) {
      const isTableVar =
        !m[1] ||
        new RegExp(`^\\s*${m[1]}\\s*=\\s*studio\\.db\\.table\\(`, 'm').test(ctx.state.doc.toString())
      if (isTableVar && m[1] !== 'studio' && m[1] !== 'api') {
        return {
          from: ctx.pos - m[2].length,
          options: TABLE_METHODS.map((name) => {
            const e = refEntry(data, `studio.db.table(name).${name}`)
            const sig = e?.signature?.replace(/^studio\.db\.table\(name\)\.\w+/, '') ?? '()'
            return snippetCompletion(`${name}(\${})`, { label: name, detail: sig, type: 'method', info: e?.doc })
          }),
          validFor: /^\w*$/,
        }
      }
    }

    // 3. inside studio.nodes.<fn>( → keyword args / enum values
    const callStart = before.lastIndexOf('studio.nodes.')
    if (callStart >= 0) {
      const rest = before.slice(callStart)
      const cm = /^studio\.nodes\.(\w+)\(([^)]*)$/.exec(rest)
      if (cm) {
        const fn = data.functions.find((f) => f.function === cm[1])
        if (fn) {
          const params = pyParams(fn)
          const enumM = /(\w+)\s*=\s*(['"]?)(\w*)$/.exec(cm[2])
          if (enumM) {
            const p = params.find((x) => x.name === enumM[1])
            if (p?.enum?.length) {
              return {
                from: ctx.pos - enumM[3].length - (enumM[2] ? 0 : 0),
                options: p.enum.map((v) => ({ label: enumM[2] ? String(v) : JSON.stringify(v), type: 'enum' })),
              }
            }
            return null
          }
          const wordM = /(\w*)$/.exec(cm[2])!
          const used = new Set([...cm[2].matchAll(/(\w+)\s*=/g)].map((x) => x[1]))
          return {
            from: ctx.pos - wordM[1].length,
            options: params
              .filter((p) => !used.has(p.name))
              .map((p) => ({
                label: `${p.name}=`,
                type: 'property',
                detail: `${p.type ?? ''}${p.required ? ' · required' : ''}${p.default != null ? ` = ${JSON.stringify(p.default)}` : ''}`,
                info: p.description ?? undefined,
                boost: p.required ? 1 : 0,
              })),
            validFor: /^\w*$/,
          }
        }
      }
    }

    // 2. studio.nodes.
    m = /studio\.nodes\.(\w*)$/.exec(before)
    if (m) {
      return {
        from: ctx.pos - m[1].length,
        options: data.functions.map((f) => {
          const c = snippetCompletion(nodeSnippet(f), {
            label: f.function,
            detail: signatureDetail(f),
            type: 'function',
            info: `${f.description} · ${returnsText(f)}${f.egress ? ' · egress' : ''}`,
          }) as NodeCompletion
          c.nodeType = f.node_type
          return c
        }),
        validFor: /^\w*$/,
      }
    }

    // studio.db.
    m = /studio\.db\.(\w*)$/.exec(before)
    if (m) {
      return {
        from: ctx.pos - m[1].length,
        options: [snippetCompletion('table("${}")', { label: 'table', detail: '(name)', type: 'function', info: refEntry(data, 'studio.db.table')?.doc })],
      }
    }

    // 1. studio.
    m = /\bstudio\.(\w*)$/.exec(before)
    if (m) {
      return {
        from: ctx.pos - m[1].length,
        options: [
          { label: 'nodes', type: 'namespace', detail: 'node functions' },
          { label: 'db', type: 'namespace', detail: 'project tables' },
          snippetCompletion('secret("${}")', { label: 'secret', type: 'function', detail: '(key)', info: refEntry(data, 'studio.secret')?.doc }),
          { label: 'api', type: 'namespace', detail: 'route decorators' },
        ],
        validFor: /^\w*$/,
      }
    }
    return null
  }
}

export function studioAutocomplete(data: CompletionData): Extension {
  return autocompletion({
    override: [studioCompletionSource(data)],
    activateOnTyping: true,
    icons: true,
    addToOptions: [
      {
        position: 90,
        render: (c: NodeCompletion) => {
          if (!c.nodeType) return null
          const s = document.createElement('span')
          s.className = 'cm-completionNodeType'
          s.setAttribute('data-testid', 'completion-type')
          s.textContent = c.nodeType
          s.style.cssText = 'margin-left:auto;padding-left:12px;font:11px var(--font-sans);color:var(--muted-foreground)'
          return s
        },
      },
    ],
  })
}
