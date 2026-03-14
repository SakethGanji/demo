import { useState } from 'react';
import { Sparkles, HelpCircle, Bug, ChevronDown, ChevronRight } from 'lucide-react';
import { ChatMessageList, ChatInput, ChatPanel, ChatPanelFooter, type ChatSuggestion } from '@/shared/components/chat';
import { useAIChatStore } from '../../stores/aiChatStore';
import { useAIChat } from '../../hooks/useAIChat';
import type { AIChatMessage } from '../../types/workflow';

/**
 * AI Chat tab content — rendered inside the shared side panel.
 */
export default function AIChatPanel() {
  const messages = useAIChatStore((s) => s.messages);
  const { sendMessage, isStreaming, cancelStream } = useAIChat();

  const suggestions: ChatSuggestion[] = [
    { label: 'Generate', icon: <Sparkles size={12} />, prompt: '' },
    { label: 'Explain', icon: <HelpCircle size={12} />, prompt: 'Explain this workflow' },
    { label: 'Fix', icon: <Bug size={12} />, prompt: 'Find and fix any issues in this workflow' },
  ];

  return (
    <ChatPanel>
      <ChatMessageList<AIChatMessage>
        messages={messages}
        isStreaming={isStreaming}
        emptyTitle="AI Workflow Assistant"
        emptyDescription="Describe a workflow to generate, or ask to modify the current one."
        renderExtra={(msg) =>
          msg.operations && msg.operations.mode !== 'explanation' ? (
            <OperationsSummary payload={msg.operations} />
          ) : null
        }
      />

      <ChatPanelFooter>
        <ChatInput
          onSend={(msg) => sendMessage(msg)}
          isStreaming={isStreaming}
          onCancel={cancelStream}
          placeholder="Describe what you want to build..."
          suggestions={suggestions}
        />
      </ChatPanelFooter>
    </ChatPanel>
  );
}

// ── Operations Summary (workflow-specific) ───────────────────────────────────

function OperationsSummary({ payload }: { payload: NonNullable<AIChatMessage['operations']> }) {
  const [expanded, setExpanded] = useState(false);

  const summary = payload.summary || '';
  let details = '';

  if (payload.mode === 'full_workflow' && payload.workflow) {
    const nodeCount = payload.workflow.nodes.length;
    const connCount = payload.workflow.connections.length;
    details = `Generated workflow with ${nodeCount} node${nodeCount !== 1 ? 's' : ''} and ${connCount} connection${connCount !== 1 ? 's' : ''}`;
  } else if (payload.mode === 'incremental' && payload.operations) {
    const counts: Record<string, number> = {};
    for (const op of payload.operations) {
      counts[op.op] = (counts[op.op] || 0) + 1;
    }
    const parts = Object.entries(counts).map(
      ([op, count]) => `${count} ${op.replace(/([A-Z])/g, ' $1').trim().toLowerCase()}`
    );
    details = parts.join(', ');
  }

  return (
    <button
      onClick={() => setExpanded(!expanded)}
      className="w-full text-left rounded-md border border-border bg-card px-2.5 py-1.5 text-xs hover:bg-accent transition-colors"
    >
      <div className="flex items-center gap-1 text-muted-foreground">
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium">Applied changes</span>
      </div>
      {expanded && (
        <div className="mt-1 text-muted-foreground space-y-0.5">
          {details && <div>{details}</div>}
          {summary && <div className="italic">{summary}</div>}
        </div>
      )}
    </button>
  );
}
