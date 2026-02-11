import { useEffect, useRef, memo } from 'react';
import { Bot, User, ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { AIChatMessage } from '../../types/aiChat';

interface AIChatMessageListProps {
  messages: AIChatMessage[];
  isStreaming: boolean;
}

export function AIChatMessageList({ messages, isStreaming }: AIChatMessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, messages[messages.length - 1]?.content]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="text-center text-muted-foreground max-w-[220px]">
          <Bot className="h-10 w-10 mx-auto mb-3 opacity-50" />
          <p className="text-sm font-medium">AI Workflow Assistant</p>
          <p className="text-xs mt-1">
            Describe a workflow to generate, or ask to modify the current one.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-3">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
      {isStreaming && (
        <div className="flex gap-2 pl-8">
          <div className="bg-muted rounded-lg px-3 py-2 flex items-center gap-1.5 h-8">
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-pulse" />
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-pulse [animation-delay:300ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-pulse [animation-delay:600ms]" />
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({ message }: { message: AIChatMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      {/* Avatar */}
      <div
        className={`h-6 w-6 rounded-full flex items-center justify-center shrink-0 ${
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted text-muted-foreground'
        }`}
      >
        {isUser ? <User size={12} /> : <Bot size={12} />}
      </div>

      {/* Content */}
      <div className={`flex flex-col gap-1 max-w-[85%] ${isUser ? 'items-end' : ''}`}>
        <div
          className={`rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words ${
            isUser
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-foreground'
          }`}
        >
          {message.content || (message.role === 'assistant' ? '\u00A0' : '')}
        </div>

        {/* Operations summary */}
        {message.operations && message.operations.mode !== 'explanation' && (
          <OperationsSummary payload={message.operations} />
        )}
      </div>
    </div>
  );
});

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
