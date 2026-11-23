import { useEffect, useRef, useMemo } from 'react';
import { EditorView, keymap } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { json } from '@codemirror/lang-json';
import { defaultKeymap } from '@codemirror/commands';
import { buildCodeTheme, watchCodeTheme } from './code-theme';

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
        themeCompartment.current.of(buildCodeTheme({ fontSize: '12px', lineHeight: '1.6', activeLine: false })),
      ],
    });

    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    const unwatch = watchCodeTheme(view, themeCompartment.current, () =>
      buildCodeTheme({ fontSize: '12px', lineHeight: '1.6', activeLine: false }),
    );

    return () => { unwatch(); view.destroy(); };
  }, [formattedJson]);

  return (
    <div
      ref={containerRef}
      className={`rounded-well overflow-auto ${className}`}
      style={{ maxHeight }}
    />
  );
}
