import { lazy, Suspense } from 'react';
import { X, ScrollText, Maximize2, Minimize2, Play } from 'lucide-react';
import { useEditorLayoutStore, type BottomPanelTab } from '../../stores/editorLayoutStore';
import { useExecutionStream } from '../../hooks/useExecutionStream';
import CodeEditor from '@/shared/components/ui/code-editor';
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
    try {
      const parsed = JSON.parse(payloadInput);
      executeWorkflow(parsed);
    } catch {
      executeWorkflow({});
    }
  };

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
