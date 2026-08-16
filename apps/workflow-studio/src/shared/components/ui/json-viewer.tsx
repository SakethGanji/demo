import { useEffect, useRef, useMemo } from 'react';
import { EditorView, keymap } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { json } from '@codemirror/lang-json';
import { defaultKeymap } from '@codemirror/commands';
import { syntaxHighlighting, HighlightStyle } from '@codemirror/language';
import { tags } from '@lezer/highlight';

// Derives all colors from the existing theme CSS variables.
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
  const purple = v('--chart-2');

  const highlight = HighlightStyle.define([
    { tag: tags.string, color: success },
    { tag: tags.number, color: warning },
    { tag: tags.bool, color: purple },
    { tag: tags.null, color: muted },
    { tag: tags.propertyName, color: primary },
    { tag: tags.punctuation, color: fg },
  ]);

  const base = EditorView.theme({
    '&': { backgroundColor: bg, color: fg },
    '.cm-content': {
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: '12px',
      lineHeight: '1.6',
    },
    '.cm-gutters': {
      backgroundColor: bg,
      borderRight: `1px solid ${border}`,
      color: muted,
    },
    '.cm-activeLine': { backgroundColor: 'transparent' },
    '.cm-activeLineGutter': { backgroundColor: 'transparent' },
    '&.cm-focused': { outline: 'none' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
      backgroundColor: `color-mix(in srgb, ${primary} 25%, transparent) !important`,
    },
  });

  return [base, syntaxHighlighting(highlight)];
}

interface JsonViewerProps {
  value: unknown;
  className?: string;
  maxHeight?: string;
}

export default function JsonViewer({ value, className = '', maxHeight = 'calc(100vh - 300px)' }: JsonViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const themeCompartment = useRef(new Compartment());

  const formattedJson = useMemo(() => {
    try {
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

  useEffect(() => {
    if (!containerRef.current) return;

    const state = EditorState.create({
      doc: formattedJson,
      extensions: [
        json(),
        EditorView.editable.of(false),
        EditorState.readOnly.of(true),
        keymap.of(defaultKeymap),
        EditorView.lineWrapping,
        themeCompartment.current.of(buildTheme()),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'class') {
          view.dispatch({ effects: themeCompartment.current.reconfigure(buildTheme()) });
        }
      }
    });
    observer.observe(document.documentElement, { attributes: true });

    return () => { observer.disconnect(); view.destroy(); };
  }, [formattedJson]);

  return (
    <div
      ref={containerRef}
      className={`rounded-md border border-border overflow-auto ${className}`}
      style={{ maxHeight }}
    />
  );
}
