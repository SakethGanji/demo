import { lazy, Suspense } from 'react';
import { X, ScrollText, Maximize2, Minimize2, Play } from 'lucide-react';
import { useEditorLayoutStore, type BottomPanelTab } from '../../stores/editorLayoutStore';
import { useExecutionStream } from '../../hooks/useExecutionStream';
import CodeEditor from '@/shared/components/ui/code-editor';
import { toast } from 'sonner';
import { cn } from '@/shared/lib/utils';

const ExecutionLogsPanel = lazy(() => import('../execution-logs/ExecutionLogsPanel'));

const tabs: { id: BottomPanelTab; label: string; icon: typeof ScrollText }[] = [
  { id: 'logs', label: 'Logs', icon: ScrollText },
  { id: 'input', label: 'Input', icon: Play },
];

export default function BottomPanel() {
  const activeTab = useEditorLayoutStore((s) => s.bottomPanelTab);
  const setTab = useEditorLayoutStore((s) => s.setBottomPanelTab);
  const closeBottomPanel = useEditorLayoutStore((s) => s.closeBottomPanel);
  const isMaximized = useEditorLayoutStore((s) => s.bottomPanelMaximized);
  const toggleMaximized = useEditorLayoutStore((s) => s.toggleBottomPanelMaximized);

  return (
    <div className="h-full flex flex-col">
      {/* Tab bar */}
      <div className="flex items-center h-9 px-2 border-b border-border/50 shrink-0">
        <div className="flex gap-0.5">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setTab(tab.id)}
              className={cn(
                'relative inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-none transition-colors',
                activeTab === tab.id
                  ? 'text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
              )}
            >
              <tab.icon size={11} />
              {tab.label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <button
          onClick={toggleMaximized}
          className="p-1 text-muted-foreground hover:text-foreground hover:bg-accent rounded"
          title={isMaximized ? 'Restore panel size' : 'Maximize panel'}
        >
          {isMaximized ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
        </button>
        <button
          onClick={closeBottomPanel}
          className="p-1 text-muted-foreground hover:text-foreground hover:bg-accent rounded"
          title="Close panel"
        >
          <X size={12} />
        </button>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden min-h-0">
        <Suspense fallback={<div className="flex-1" />}>
          {activeTab === 'logs' && <ExecutionLogsPanel />}
          {activeTab === 'input' && <InputPanel />}
        </Suspense>
      </div>
    </div>
  );
}

function InputPanel() {
  const payloadInput = useEditorLayoutStore((s) => s.payloadInput);
  const setPayloadInput = useEditorLayoutStore((s) => s.setPayloadInput);
  const { executeWorkflow } = useExecutionStream();

  const handleRunWithPayload = () => {
    if (!payloadInput || !payloadInput.trim()) {
      executeWorkflow({});
      return;
    }
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(payloadInput);
    } catch (e) {
      // Forgiving retry: pasted JSON often contains raw newlines/tabs inside
      // string literals (browser wrap on copy, etc.) which JSON.parse rejects
      // as "Bad control character". Walk a tiny state machine, escape control
      // chars only inside string literals, and retry once.
      const fixed = escapeControlCharsInJsonStrings(payloadInput);
      if (fixed !== payloadInput) {
        try {
          parsed = JSON.parse(fixed);
          toast.warning('Auto-fixed unescaped control chars in JSON strings');
          executeWorkflow(parsed);
          return;
        } catch {
          // fall through to original error
        }
      }
      toast.error(`Payload JSON invalid: ${(e as Error).message}`);
      return;
    }
    executeWorkflow(parsed);
  };

  // Walk char-by-char; inside a JSON string literal (delimited by unescaped
  // double-quote), replace raw control chars (CR/LF/TAB/etc.) with their JSON
  // escape sequence. Outside string literals, control chars are valid whitespace
  // and are preserved.
  function escapeControlCharsInJsonStrings(text: string): string {
    let out = '';
    let inString = false;
    let escaped = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (escaped) {
        out += ch;
        escaped = false;
        continue;
      }
      if (ch === '\\') {
        out += ch;
        escaped = true;
        continue;
      }
      if (ch === '"') {
        inString = !inString;
        out += ch;
        continue;
      }
      if (inString && ch.charCodeAt(0) < 0x20) {
        if (ch === '\n') out += '\\n';
        else if (ch === '\r') out += '\\r';
        else if (ch === '\t') out += '\\t';
        else out += `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`;
        continue;
      }
      out += ch;
    }
    return out;
  }

  const handleRunWithoutPayload = () => {
    executeWorkflow({});
  };

  return (
    <div className="h-full relative">
      <CodeEditor
        value={payloadInput}
        onChange={setPayloadInput}
        language="json"
        height="100%"
      />
      <div className="absolute bottom-3 right-3 flex items-center gap-2">
        <button
          onClick={handleRunWithoutPayload}
          className="h-7 px-3 rounded-md border border-border bg-card text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-accent flex items-center gap-1.5"
        >
          <Play size={11} />
          Run without Payload
        </button>
        <button
          onClick={handleRunWithPayload}
          className="h-7 px-3 rounded-md bg-[var(--success)] text-primary-foreground text-[12px] font-medium hover:brightness-110 flex items-center gap-1.5"
        >
          <Play size={11} fill="currentColor" />
          Run with Payload
        </button>
      </div>
    </div>
  );
}
