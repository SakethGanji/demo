import { useEffect, useRef, useMemo } from 'react';
import { EditorView, keymap } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { json } from '@codemirror/lang-json';
import { defaultKeymap } from '@codemirror/commands';
import { syntaxHighlighting, HighlightStyle } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { oneDark } from '@codemirror/theme-one-dark';

interface JsonViewerProps {
  value: unknown;
  className?: string;
  maxHeight?: string;
}

// Light theme highlighting
const lightHighlightStyle = HighlightStyle.define([
  { tag: tags.string, color: '#22863a' },
  { tag: tags.number, color: '#005cc5' },
  { tag: tags.bool, color: '#6f42c1' },
  { tag: tags.null, color: '#6a737d' },
  { tag: tags.propertyName, color: '#005cc5' },
  { tag: tags.punctuation, color: '#24292e' },
]);

// Light theme base styles
const lightTheme = EditorView.theme({
  '&': {
    backgroundColor: '#fafafa',
    color: '#24292e',
  },
  '.cm-content': {
    caretColor: '#24292e',
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: '12px',
    lineHeight: '1.6',
  },
  '.cm-gutters': {
    backgroundColor: '#fafafa',
    borderRight: '1px solid #e1e4e8',
    color: '#6a737d',
  },
  '.cm-activeLine': {
    backgroundColor: 'transparent',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
  },
  '&.cm-focused': {
    outline: 'none',
  },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
    backgroundColor: '#b3d4fc !important',
  },
});

// Dark theme base styles (complementing oneDark)
const darkTheme = EditorView.theme({
  '&': {
    backgroundColor: '#18181b',
  },
  '.cm-content': {
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: '12px',
    lineHeight: '1.6',
  },
  '.cm-gutters': {
    backgroundColor: '#18181b',
    borderRight: '1px solid #27272a',
  },
  '.cm-activeLine': {
    backgroundColor: 'transparent',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
  },
  '&.cm-focused': {
    outline: 'none',
  },
});

export default function JsonViewer({ value, className = '', maxHeight = 'calc(100vh - 300px)' }: JsonViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const themeCompartment = useRef(new Compartment());

  const formattedJson = useMemo(() => {
    try {
      // Truncate very long string values to keep CodeMirror performant
      const truncated = JSON.parse(JSON.stringify(value, (_key, val) => {
        if (typeof val === 'string' && val.length > 32768) {
          return val.slice(0, 32768) + `\n... (${val.length.toLocaleString()} chars truncated)`;
        }
        return val;
      }));
      return JSON.stringify(truncated, null, 2);
    } catch {
      return String(value);
    }
  }, [value]);

  // Detect current theme
  const isDark = useMemo(() => {
    if (typeof window !== 'undefined') {
      return document.documentElement.classList.contains('dark');
    }
    return false;
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const getThemeExtensions = (dark: boolean) => {
      return dark
        ? [darkTheme, oneDark]
        : [lightTheme, syntaxHighlighting(lightHighlightStyle)];
    };

    const state = EditorState.create({
      doc: formattedJson,
      extensions: [
        json(),
        EditorView.editable.of(false),
        EditorState.readOnly.of(true),
        keymap.of(defaultKeymap),
        EditorView.lineWrapping,
        themeCompartment.current.of(getThemeExtensions(isDark)),
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
  }, [formattedJson, isDark]);

  return (
    <div
      ref={containerRef}
      className={`rounded-md border border-border overflow-auto ${className}`}
      style={{ maxHeight }}
    />
  );
}
