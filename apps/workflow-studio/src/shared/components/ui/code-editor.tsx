import { useEffect, useRef, useCallback } from 'react';
import { EditorView, keymap, placeholder as placeholderExt } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { json } from '@codemirror/lang-json';
import { javascript } from '@codemirror/lang-javascript';
import { defaultKeymap, indentWithTab } from '@codemirror/commands';
import { syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching, foldGutter, foldKeymap } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { lineNumbers, highlightActiveLineGutter, highlightActiveLine } from '@codemirror/view';
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { history, historyKeymap } from '@codemirror/commands';

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language?: 'json' | 'javascript';
  placeholder?: string;
  minHeight?: string;
  maxHeight?: string;
  className?: string;
}

// Maps syntax tokens to existing theme CSS variables so the editor
// automatically follows whatever palette is defined in index.css.
function buildTheme() {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string) => s.getPropertyValue(name).trim();

  const fg = v('--foreground');
  const muted = v('--muted-foreground');
  const bg = v('--secondary');
  const border = v('--border');
  const primary = v('--primary');
  const success = v('--success');
  const warning = v('--warning');
  const destructive = v('--destructive');
  const purple = v('--chart-2');

  const highlight = HighlightStyle.define([
    { tag: tags.string, color: success },
    { tag: tags.number, color: warning },
    { tag: tags.bool, color: purple },
    { tag: tags.null, color: muted },
    { tag: tags.propertyName, color: primary },
    { tag: tags.punctuation, color: fg },
    { tag: tags.keyword, color: destructive },
    { tag: tags.function(tags.variableName), color: purple },
    { tag: tags.variableName, color: fg },
    { tag: tags.comment, color: muted, fontStyle: 'italic' },
    { tag: tags.operator, color: destructive },
    { tag: tags.className, color: purple },
    { tag: tags.definition(tags.variableName), color: warning },
  ]);

  const base = EditorView.theme({
    '&': { backgroundColor: bg, color: fg },
    '.cm-content': {
      caretColor: fg,
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: '13px',
      lineHeight: '1.5',
      padding: '8px 0',
    },
    '.cm-gutters': {
      backgroundColor: bg,
      borderRight: `1px solid ${border}`,
      color: muted,
    },
    '.cm-lineNumbers .cm-gutterElement': { padding: '0 8px 0 12px', minWidth: '32px' },
    '.cm-activeLine': { backgroundColor: `color-mix(in srgb, ${fg} 4%, transparent)` },
    '.cm-activeLineGutter': { backgroundColor: `color-mix(in srgb, ${fg} 4%, transparent)` },
    '&.cm-focused': { outline: 'none' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
      backgroundColor: `color-mix(in srgb, ${primary} 25%, transparent) !important`,
    },
    '.cm-placeholder': { color: muted, fontStyle: 'italic' },
    '.cm-foldGutter': { width: '12px' },
  });

  return [base, syntaxHighlighting(highlight)];
}

export default function CodeEditor({
  value,
  onChange,
  language = 'json',
  placeholder,
  minHeight = '150px',
  maxHeight = '400px',
  className = '',
}: CodeEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const themeCompartment = useRef(new Compartment());
  const onChangeRef = useRef(onChange);

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);

  const getLanguageExtension = useCallback(() => {
    return language === 'javascript' ? javascript() : json();
  }, [language]);

  useEffect(() => {
    if (!containerRef.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        getLanguageExtension(),
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        foldGutter(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...foldKeymap, indentWithTab]),
        EditorView.lineWrapping,
        themeCompartment.current.of(buildTheme()),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) onChangeRef.current(update.state.doc.toString());
        }),
        ...(placeholder ? [placeholderExt(placeholder)] : []),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    // Rebuild theme when light/dark class changes
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'class') {
          view.dispatch({ effects: themeCompartment.current.reconfigure(buildTheme()) });
        }
      }
    });
    observer.observe(document.documentElement, { attributes: true });

    return () => { observer.disconnect(); view.destroy(); };
  }, [getLanguageExtension, placeholder]);

  useEffect(() => {
    const view = viewRef.current;
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
    }
  }, [value]);

  return (
    <div
      ref={containerRef}
      className={`rounded-md border border-input overflow-auto focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/20 ${className}`}
      style={{ minHeight, maxHeight }}
    />
  );
}
