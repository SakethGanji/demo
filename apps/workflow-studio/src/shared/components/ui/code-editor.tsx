import { useEffect, useRef, useMemo, useCallback } from 'react';
import { EditorView, keymap, placeholder as placeholderExt } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { json } from '@codemirror/lang-json';
import { javascript } from '@codemirror/lang-javascript';
import { defaultKeymap, indentWithTab } from '@codemirror/commands';
import { syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching, foldGutter, foldKeymap } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { oneDark } from '@codemirror/theme-one-dark';
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

// Light theme highlighting
const lightHighlightStyle = HighlightStyle.define([
  { tag: tags.string, color: '#22863a' },
  { tag: tags.number, color: '#005cc5' },
  { tag: tags.bool, color: '#6f42c1' },
  { tag: tags.null, color: '#6a737d' },
  { tag: tags.propertyName, color: '#005cc5' },
  { tag: tags.punctuation, color: '#24292e' },
  { tag: tags.keyword, color: '#d73a49' },
  { tag: tags.function(tags.variableName), color: '#6f42c1' },
  { tag: tags.variableName, color: '#24292e' },
  { tag: tags.comment, color: '#6a737d', fontStyle: 'italic' },
  { tag: tags.operator, color: '#d73a49' },
  { tag: tags.className, color: '#6f42c1' },
  { tag: tags.definition(tags.variableName), color: '#e36209' },
]);

// Light theme base styles
const lightTheme = EditorView.theme({
  '&': {
    backgroundColor: 'var(--secondary)',
    color: '#24292e',
  },
  '.cm-content': {
    caretColor: '#24292e',
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: '13px',
    lineHeight: '1.5',
    padding: '8px 0',
  },
  '.cm-gutters': {
    backgroundColor: 'var(--secondary)',
    borderRight: '1px solid var(--border)',
    color: '#6a737d',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 8px 0 12px',
    minWidth: '32px',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(0, 0, 0, 0.04)',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'rgba(0, 0, 0, 0.04)',
  },
  '&.cm-focused': {
    outline: 'none',
  },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
    backgroundColor: '#b3d4fc !important',
  },
  '.cm-placeholder': {
    color: '#6a737d',
    fontStyle: 'italic',
  },
  '.cm-foldGutter': {
    width: '12px',
  },
});

// Dark theme base styles (complementing oneDark)
const darkTheme = EditorView.theme({
  '&': {
    backgroundColor: 'var(--secondary)',
  },
  '.cm-content': {
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: '13px',
    lineHeight: '1.5',
    padding: '8px 0',
  },
  '.cm-gutters': {
    backgroundColor: 'var(--secondary)',
    borderRight: '1px solid var(--border)',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 8px 0 12px',
    minWidth: '32px',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
  },
  '&.cm-focused': {
    outline: 'none',
  },
  '.cm-placeholder': {
    color: '#6a737d',
    fontStyle: 'italic',
  },
  '.cm-foldGutter': {
    width: '12px',
  },
});

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

  // Keep onChange ref up to date
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  // Detect current theme
  const isDark = useMemo(() => {
    if (typeof window !== 'undefined') {
      return document.documentElement.classList.contains('dark');
    }
    return false;
  }, []);

  const getThemeExtensions = useCallback((dark: boolean) => {
    return dark
      ? [darkTheme, oneDark]
      : [lightTheme, syntaxHighlighting(lightHighlightStyle)];
  }, []);

  const getLanguageExtension = useCallback(() => {
    return language === 'javascript' ? javascript() : json();
  }, [language]);

  useEffect(() => {
    if (!containerRef.current) return;

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const newValue = update.state.doc.toString();
        onChangeRef.current(newValue);
      }
    });

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
        keymap.of([
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...historyKeymap,
          ...foldKeymap,
          indentWithTab,
        ]),
        EditorView.lineWrapping,
        themeCompartment.current.of(getThemeExtensions(isDark)),
        updateListener,
        ...(placeholder ? [placeholderExt(placeholder)] : []),
      ],
    });

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });

    viewRef.current = view;

    // Watch for theme changes
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'class') {
          const dark = document.documentElement.classList.contains('dark');
          view.dispatch({
            effects: themeCompartment.current.reconfigure(getThemeExtensions(dark)),
          });
        }
      });
    });

    observer.observe(document.documentElement, { attributes: true });

    return () => {
      observer.disconnect();
      view.destroy();
    };
  }, [isDark, getThemeExtensions, getLanguageExtension, placeholder]);

  // Update editor content when value prop changes externally
  useEffect(() => {
    const view = viewRef.current;
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({
        changes: {
          from: 0,
          to: view.state.doc.length,
          insert: value,
        },
      });
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
