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
import { indentOnInput, bracketMatching, foldGutter, foldKeymap, indentUnit } from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { setDiagnostics, type Diagnostic } from '@codemirror/lint'
import { buildCodeTheme, watchCodeTheme } from './code-theme'

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
        cc.theme.of(buildCodeTheme({ background: !bare })),
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

    const unwatch = watchCodeTheme(view, cc.theme, () => buildCodeTheme({ background: !bare }))
    return () => {
      unwatch()
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
      className={`${bare ? '' : 'overflow-hidden rounded-well focus-within:shadow-focus'} overflow-auto ${className}`}
      style={height ? { height } : { minHeight, maxHeight }}
    />
  )
})

export default CodeEditor
