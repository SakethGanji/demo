import { lazy, Suspense, useMemo } from 'react';
import { X, ScrollText, Monitor, Maximize2, Minimize2 } from 'lucide-react';
import { useEditorLayoutStore, type BottomPanelTab } from '../../stores/editorLayoutStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { detectUINodes } from '../ui-preview/detectUINodes';
import { ChatPanel } from '../ui-preview/ChatPanel';
import { ChatInput } from '../ui-preview/ChatInput';
import { HTMLPanel } from '../ui-preview/HTMLPanel';
import { MarkdownPanel } from '../ui-preview/MarkdownPanel';
import { MessageSquare } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { WorkflowNodeData } from '../../types/workflow';
import type { Node } from 'reactflow';

const ExecutionLogsPanel = lazy(() => import('../execution-logs/ExecutionLogsPanel'));

const tabs: { id: BottomPanelTab; label: string; icon: typeof ScrollText }[] = [
  { id: 'logs', label: 'Logs', icon: ScrollText },
  { id: 'ui', label: 'UI', icon: Monitor },
];

export default function BottomPanel() {
  const activeTab = useEditorLayoutStore((s) => s.bottomPanelTab);
  const setTab = useEditorLayoutStore((s) => s.setBottomPanelTab);
  const closeBottomPanel = useEditorLayoutStore((s) => s.closeBottomPanel);
  const isMaximized = useEditorLayoutStore((s) => s.bottomPanelMaximized);
  const toggleMaximized = useEditorLayoutStore((s) => s.toggleBottomPanelMaximized);

  const nodes = useWorkflowStore((s) => s.nodes) as Node<WorkflowNodeData>[];
  const uiConfig = useMemo(() => detectUINodes(nodes), [nodes]);

  return (
    <div className="editor-chrome h-full flex flex-col bg-card">
      {/* Tab bar */}
      <div className="flex items-center h-8 px-2 border-b border-border shrink-0">
        <div className="flex gap-0.5">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setTab(tab.id)}
              className={cn(
                'inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors',
                activeTab === tab.id
                  ? 'bg-accent text-foreground'
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
          {activeTab === 'ui' && <UIContent uiConfig={uiConfig} />}
        </Suspense>
      </div>
    </div>
  );
}

function UIContent({ uiConfig }: { uiConfig: ReturnType<typeof detectUINodes> }) {
  const hasChat = uiConfig.outputTypes.includes('chat');
  const hasHTML = uiConfig.outputTypes.includes('html');
  const hasMarkdown = uiConfig.outputTypes.includes('markdown');
  const hasAnyOutput = hasChat || hasHTML || hasMarkdown;

  if (!uiConfig.inputNode && uiConfig.outputNodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-4 h-full">
        <div className="text-center text-muted-foreground max-w-[200px]">
          <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-xs font-medium">No UI nodes</p>
          <p className="text-[10px] mt-1">
            Add a ChatInput node to enable the chat interface
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden min-h-0">
      <div className="flex-1 overflow-auto p-3 space-y-3">
        {hasChat && <ChatPanel />}
        {hasMarkdown && <MarkdownPanel />}
        {hasHTML && <HTMLPanel />}
        {!hasAnyOutput && (
          <div className="h-full flex items-center justify-center text-muted-foreground text-xs">
            No output nodes configured
          </div>
        )}
      </div>

      {uiConfig.inputType === 'chat' && (
        <div className="p-3 border-t">
          <ChatInput config={uiConfig} />
        </div>
      )}
      {!uiConfig.inputType && (
        <div className="p-3 border-t text-center text-[10px] text-muted-foreground">
          Add a ChatInput node to enable user input
        </div>
      )}
    </div>
  );
}
