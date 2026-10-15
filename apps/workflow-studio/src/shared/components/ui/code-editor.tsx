/**
 * CodeMirror 6 editor (UI-DESIGN §7.1, review B5).
 *
 * The EditorView is created once and never recreated on prop changes: language, theme,
 * readOnly, line wrapping, placeholder and caller extensions live in Compartments and are
 * reconfigured in place. `editorRef` exposes the view and `applyTransaction` so callers can
 * apply AI drafts as one undoable transaction.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { EditorView, keymap, placeholder as placeholderExt, lineNumbers, highlightActiveLineGutter, highlightActiveLine } from '@codemirror/view'
import { EditorState, Compartment, type Extension, type TransactionSpec } from '@codemirror/state'
import { json } from '@codemirror/lang-json'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { defaultKeymap, indentWithTab, history, historyKeymap } from '@codemirror/commands'
import { syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching, foldGutter, foldKeymap, indentUnit } from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { setDiagnostics, type Diagnostic } from '@codemirror/lint'
import { tags } from '@lezer/highlight'

export type CodeLanguage = 'json' | 'javascript' | 'python'

export interface CodeEditorHandle {
  view: EditorView | null
  applyTransaction: (spec: TransactionSpec) => void
}

export interface CodeEditorProps {
  value: string
  onChange?: (value: string) => void
  language?: CodeLanguage
  placeholder?: string
  minHeight?: string
  maxHeight?: string
  /** fixed height (e.g. '100%') */
  height?: string
  className?: string
  readOnly?: boolean
  /** caller extensions (lint gutter, widgets, completions…), reconfigured in place */
  extensions?: Extension[]
  /** lint diagnostics pushed with `setDiagnostics` (needs a lint extension, added automatically) */
  diagnostics?: Diagnostic[]
  /** soft-wrap long lines (default: true for json/javascript, false for python) */
  wrap?: boolean
  onReady?: (view: EditorView) => void
  testId?: string
  /** drop the outer border/rounding (when embedded in a panel) */
  bare?: boolean
}

function cssVar(s: CSSStyleDeclaration, name: string, fallback: string) {
  return s.getPropertyValue(name).trim() || fallback
}

/** Reads `--code-bg`, `--code-gutter` and `--syn-*` so the editor follows the theme. */
function buildTheme(): Extension {
  const s = getComputedStyle(document.documentElement)
  const fg = cssVar(s, '--foreground', '#1e1e1e')
  const codeBg = cssVar(s, '--code-bg', '#fbfbfc')
  const gutter = cssVar(s, '--code-gutter', '#80878e')
  const border = cssVar(s, '--border', '#dee2e6')
  const primary = cssVar(s, '--primary', '#18a0fb')
  const kw = cssVar(s, '--syn-keyword', '#c2185b')
  const str = cssVar(s, '--syn-string', '#0d7a41')
  const num = cssVar(s, '--syn-number', '#94600a')
  const fn = cssVar(s, '--syn-fn', '#0b6fc0')
  const comment = cssVar(s, '--syn-comment', '#737a82')
  const deco = cssVar(s, '--syn-deco', '#94600a')

  const highlight = HighlightStyle.define([
    { tag: [tags.keyword, tags.controlKeyword, tags.operatorKeyword, tags.definitionKeyword, tags.moduleKeyword], color: kw },
    { tag: [tags.string, tags.special(tags.string)], color: str },
    { tag: [tags.number, tags.bool, tags.null], color: num },
    { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: fn },
    { tag: tags.definition(tags.function(tags.variableName)), color: fn },
    { tag: tags.propertyName, color: fn },
    { tag: tags.className, color: fn },
    { tag: tags.meta, color: deco },
    { tag: tags.comment, color: comment, fontStyle: 'italic' },
    { tag: tags.variableName, color: fg },
    { tag: tags.punctuation, color: fg },
    { tag: tags.operator, color: fg },
  ])

  const base = EditorView.theme({
    '&': { backgroundColor: codeBg, color: fg, height: '100%' },
    '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '20px' },
    '.cm-content': { caretColor: fg, fontSize: '12.5px', padding: '10px 0' },
    '.cm-gutters': { backgroundColor: codeBg, borderRight: 'none', color: gutter },
    '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 12px', minWidth: '40px' },
    '.cm-activeLine': { backgroundColor: `color-mix(in srgb, ${primary} 5%, transparent)` },
    '.cm-activeLineGutter': { backgroundColor: 'transparent', color: fg },
    '&.cm-focused': { outline: 'none' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': {
      backgroundColor: `color-mix(in srgb, ${primary} 22%, transparent) !important`,
    },
    '.cm-placeholder': { color: gutter, fontStyle: 'italic' },
    '.cm-foldGutter': { width: '12px' },
    '.cm-tooltip': { border: `1px solid ${border}`, borderRadius: '8px', overflow: 'hidden' },
  })
  return [base, syntaxHighlighting(highlight)]
}

function languageExt(lang: CodeLanguage): Extension {
  if (lang === 'python') return [python(), indentUnit.of('    ')]
  if (lang === 'javascript') return javascript()
  return json()
}

const CodeEditor = forwardRef<CodeEditorHandle, CodeEditorProps>(function CodeEditor(
  {
    value,
    onChange,
    language = 'json',
    placeholder,
    minHeight = '150px',
    maxHeight = '400px',
    height,
    className = '',
    readOnly = false,
    extensions,
    diagnostics,
    wrap,
    onReady,
    testId,
    bare = false,
  },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const c = useRef({
    lang: new Compartment(),
    theme: new Compartment(),
    readOnly: new Compartment(),
    wrap: new Compartment(),
    placeholder: new Compartment(),
    extra: new Compartment(),
  })
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const shouldWrap = wrap ?? language !== 'python'

  useImperativeHandle(ref, () => ({
    get view() {
      return viewRef.current
    },
    applyTransaction: (spec: TransactionSpec) => viewRef.current?.dispatch(spec),
  }))

  // Create the view once.
  useEffect(() => {
    if (!containerRef.current) return
    const cc = c.current
    const state = EditorState.create({
      doc: value,
      extensions: [
        cc.lang.of(languageExt(language)),
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        foldGutter(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        cc.extra.of(extensions ?? []),
        keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...foldKeymap, indentWithTab]),
        cc.wrap.of(shouldWrap ? EditorView.lineWrapping : []),
        cc.theme.of(buildTheme()),
        cc.readOnly.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
        cc.placeholder.of(placeholder ? placeholderExt(placeholder) : []),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChangeRef.current?.(u.state.doc.toString())
        }),
      ],
    })
    const view = new EditorView({ state, parent: containerRef.current })
    viewRef.current = view
    onReady?.(view)

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'class') view.dispatch({ effects: cc.theme.reconfigure(buildTheme()) })
      }
    })
    observer.observe(document.documentElement, { attributes: true })
    return () => {
      observer.disconnect()
      view.destroy()
      viewRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reconfigure in place.
  useEffect(() => {
    viewRef.current?.dispatch({ effects: c.current.lang.reconfigure(languageExt(language)) })
  }, [language])
  useEffect(() => {
    viewRef.current?.dispatch({
      effects: c.current.readOnly.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    })
  }, [readOnly])
  useEffect(() => {
    viewRef.current?.dispatch({ effects: c.current.wrap.reconfigure(shouldWrap ? EditorView.lineWrapping : []) })
  }, [shouldWrap])
  useEffect(() => {
    viewRef.current?.dispatch({ effects: c.current.placeholder.reconfigure(placeholder ? placeholderExt(placeholder) : []) })
  }, [placeholder])
  useEffect(() => {
    viewRef.current?.dispatch({ effects: c.current.extra.reconfigure(extensions ?? []) })
  }, [extensions])
  useEffect(() => {
    const view = viewRef.current
    if (view && diagnostics) view.dispatch(setDiagnostics(view.state, diagnostics))
  }, [diagnostics])

  // External value changes (e.g. switching documents) replace the doc.
  useEffect(() => {
    const view = viewRef.current
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value }, userEvent: 'external' })
    }
  }, [value])

  return (
    <div
      ref={containerRef}
      data-testid={testId}
      data-scroll-x="allowed"
      className={`${bare ? '' : 'rounded-md border border-input focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/20'} overflow-auto ${className}`}
      style={height ? { height } : { minHeight, maxHeight }}
    />
  )
})

export default CodeEditor
