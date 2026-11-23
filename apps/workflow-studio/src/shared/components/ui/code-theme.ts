/**
 * The one CodeMirror look (DESIGN §5, §8): background `--code-bg`, gutter `--code-gutter`, syntax `--syn-*`,
 * selection in the accent tint, tooltips and autocomplete as glass overlays. Every colour is read from the
 * tokens at build time (no hex fallbacks); `watchCodeTheme` rebuilds it when the theme flips.
 */
import { EditorView } from '@codemirror/view'
import type { Compartment, Extension } from '@codemirror/state'
import { syntaxHighlighting, HighlightStyle } from '@codemirror/language'
import { tags } from '@lezer/highlight'

function tokens() {
  const s = getComputedStyle(document.documentElement)
  const v = (name: string) => s.getPropertyValue(name).trim()
  return {
    fg: v('--foreground'),
    muted: v('--muted-foreground'),
    codeBg: v('--code-bg'),
    gutter: v('--code-gutter'),
    ring: v('--ring'),
    primary: v('--primary'),
    hairline: v('--hairline'),
    overlay: v('--overlay-fill'),
    overlayBlur: v('--overlay-blur'),
    overlayShadow: v('--overlay-shadow'),
    raise: v('--raise'),
    raiseShadow: v('--raise-sm'),
    kw: v('--syn-keyword'),
    str: v('--syn-string'),
    num: v('--syn-number'),
    fn: v('--syn-fn'),
    comment: v('--syn-comment'),
    deco: v('--syn-deco'),
  }
}

export interface CodeThemeOptions {
  /** font size of the content, default 12.5px */
  fontSize?: string
  /** line height, default 20px */
  lineHeight?: string
  /** paint the editor background (`--code-bg`); off when the editor sits in a well that paints it */
  background?: boolean
  /** show the active-line tint, default true */
  activeLine?: boolean
}

/** The editor theme + syntax highlighting, built from the current token values. */
export function buildCodeTheme({ fontSize = '12.5px', lineHeight = '20px', background = true, activeLine = true }: CodeThemeOptions = {}): Extension {
  const t = tokens()
  const bg = background ? t.codeBg : 'transparent'
  const highlight = HighlightStyle.define([
    { tag: [tags.keyword, tags.controlKeyword, tags.operatorKeyword, tags.definitionKeyword, tags.moduleKeyword], color: t.kw },
    { tag: [tags.string, tags.special(tags.string)], color: t.str },
    { tag: [tags.number, tags.bool, tags.null], color: t.num },
    { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: t.fn },
    { tag: tags.definition(tags.function(tags.variableName)), color: t.fn },
    { tag: tags.propertyName, color: t.fn },
    { tag: tags.className, color: t.fn },
    { tag: tags.meta, color: t.deco },
    { tag: tags.comment, color: t.comment, fontStyle: 'italic' },
    { tag: tags.variableName, color: t.fg },
    { tag: tags.punctuation, color: t.fg },
    { tag: tags.operator, color: t.fg },
  ])
  const base = EditorView.theme({
    '&': { backgroundColor: bg, color: t.fg, height: '100%' },
    '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight, fontFeatureSettings: 'var(--font-features-mono)' },
    '.cm-content': { caretColor: t.fg, fontSize, padding: '10px 0' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: t.fg },
    '.cm-gutters': { backgroundColor: bg, borderRight: 'none', color: t.gutter },
    '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 12px', minWidth: '40px' },
    '.cm-activeLine': { backgroundColor: activeLine ? `color-mix(in srgb, ${t.primary} 6%, transparent)` : 'transparent' },
    '.cm-activeLineGutter': { backgroundColor: 'transparent', color: activeLine ? t.fg : t.gutter },
    '&.cm-focused': { outline: 'none' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': {
      backgroundColor: `color-mix(in srgb, ${t.primary} 22%, transparent) !important`,
    },
    '.cm-placeholder': { color: t.gutter, fontStyle: 'italic' },
    '.cm-foldGutter': { width: '12px' },
    '.cm-foldPlaceholder': { backgroundColor: 'transparent', border: 'none', color: t.muted },
    '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': { backgroundColor: `color-mix(in srgb, ${t.ring} 18%, transparent)`, outline: 'none' },
    '.cm-tooltip': {
      border: 'none',
      borderRadius: '8px',
      overflow: 'hidden',
      backgroundColor: t.overlay,
      backdropFilter: t.overlayBlur, // design-ok: CodeMirror tooltip is an overlay
      WebkitBackdropFilter: t.overlayBlur,
      boxShadow: t.overlayShadow,
      color: t.fg,
    },
    '.cm-tooltip-autocomplete > ul': { fontFamily: 'var(--font-mono)', fontSize: '12px', padding: '4px' },
    '.cm-tooltip-autocomplete > ul > li': { borderRadius: '4px', padding: '2px 6px' },
    '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: t.raise, color: t.fg, boxShadow: t.raiseShadow },
    '.cm-tooltip-lint': { padding: '2px' },
    '.cm-diagnostic': { borderLeftWidth: '3px', padding: '4px 8px' },
    '.cm-panels': { backgroundColor: bg, color: t.fg, borderColor: t.hairline },
  })
  return [base, syntaxHighlighting(highlight)]
}

/**
 * Rebuilds the theme in `compartment` whenever `.dark` flips on <html>. Returns the disconnect function.
 */
export function watchCodeTheme(view: EditorView, compartment: Compartment, build: () => Extension) {
  const root = document.documentElement
  let dark = root.classList.contains('dark')
  const observer = new MutationObserver(() => {
    const now = root.classList.contains('dark')
    if (now === dark) return
    dark = now
    view.dispatch({ effects: compartment.reconfigure(build()) })
  })
  observer.observe(root, { attributes: true, attributeFilter: ['class'] })
  return () => observer.disconnect()
}
