import { Sparkles, HelpCircle, Bug } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { ChatMessageList, ChatInput, ChatPanel, ChatPanelFooter, type ChatSuggestion } from '@/shared/components/chat';
import { ActorPrompt } from '@/features/ai-builder/components/ActorPrompt';
import { useActorName } from '@/features/ai-builder/useActorName';
import { useAIChatStore } from '../../stores/aiChatStore';
import { useEditorProjectStore } from '../../stores/editorProjectStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useAIChat } from '../../hooks/useAIChat';
import { approveChatSpec } from '../../lib/aiChatClient';
import type { AIChatMessage } from '../../types/workflow';
import { ProposalChatCard, SpecChatCard } from './ChatCards';
import { TemplateSuggestionCard } from './TemplateSuggestionCard';

/**
 * AI Chat tab content — rendered inside the shared side panel.
 * It answers questions, and turns a request into a plan (new workflow) or a reviewed proposal (change); it never edits the canvas.
 */
export default function AIChatPanel() {
  const messages = useAIChatStore((s) => s.messages);
  const { sendMessage, isStreaming, cancelStream } = useAIChat();
  const actor = useActorName();
  const projectId = useEditorProjectStore((s) => s.projectId);
  const slug = useEditorProjectStore((s) => s.slug);
  const navigate = useNavigate();

  const suggestions: ChatSuggestion[] = [
    { label: 'Generate', icon: <Sparkles size={12} />, prompt: '' },
    { label: 'Explain', icon: <HelpCircle size={12} />, prompt: 'Explain this workflow' },
    { label: 'Fix', icon: <Bug size={12} />, prompt: 'Find and fix any issues in this workflow' },
  ];

  // The plan and the review live in the Build tab of this same page: open it on that proposal.
  const openReview = (proposalId: string) => {
    useEditorLayoutStore.getState().ensureRightPanelOpen('builder');
    void navigate({
      to: '/p/$project/workflows/$workflowId',
      params: { project: slug, workflowId: useWorkflowStore.getState().workflowId ?? 'new' },
      search: (prev: Record<string, unknown>) => ({ ...prev, proposal: proposalId }),
    });
  };

  // Every change is recorded under a name; ask once before the first message.
  if (!actor) {
    return (
      <ChatPanel>
        <div className="p-4"><ActorPrompt /></div>
      </ChatPanel>
    );
  }

  return (
    <ChatPanel>
      <ChatMessageList<AIChatMessage>
        messages={messages}
        isStreaming={isStreaming}
        emptyTitle="AI Workflow Assistant"
        emptyDescription="Ask about this workflow, describe a change, or describe a new workflow. Changes are shown to you first; nothing is saved until you keep them."
        renderExtra={(msg) => {
          const card = msg.card;
          return (
            <>
              {msg.status && isStreaming && <div className="px-1 text-[12px] text-muted-foreground" data-testid="ai-chat-status">{msg.status}</div>}
              {card?.kind === 'spec' && (
                <SpecChatCard card={card} onApprove={() => void approveChatSpec(msg.id, actor, projectId)} onOpen={() => openReview(card.proposalId)} />
              )}
              {card?.kind === 'spec' && card.phase === 'draft' && (
                <TemplateSuggestionCard proposalId={card.proposalId} projectId={projectId} projectSlug={slug} />
              )}
              {card?.kind === 'proposal' && <ProposalChatCard card={card} onOpen={() => openReview(card.proposalId)} />}
            </>
          );
        }}
      />

      <ChatPanelFooter>
        <ChatInput
          onSend={(msg) => sendMessage(msg)}
          isStreaming={isStreaming}
          onCancel={cancelStream}
          placeholder="Ask a question or describe a change..."
          suggestions={suggestions}
        />
      </ChatPanelFooter>
    </ChatPanel>
  );
}
