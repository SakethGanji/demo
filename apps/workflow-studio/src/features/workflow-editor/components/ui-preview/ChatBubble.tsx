import { memo } from 'react';
import { cn } from '@/shared/lib/utils';
import type { UIMessage } from '../../stores/uiModeStore';

interface ChatBubbleProps {
  message: UIMessage;
}

export const ChatBubble = memo(function ChatBubble({ message }: ChatBubbleProps) {
  const isUser = message.type === 'user';
  const isSystem = message.type === 'system';

  return (
    <div
      className={cn(
        'flex w-full',
        isUser ? 'justify-end' : 'justify-start'
      )}
    >
      <div
        className={cn(
          'max-w-[80%] rounded-2xl px-4 py-2.5',
          isUser && 'bg-primary text-primary-foreground rounded-br-md',
          !isUser && !isSystem && 'bg-muted rounded-bl-md',
          isSystem && 'bg-muted/50 text-muted-foreground text-sm italic'
        )}
      >
        {message.format === 'markdown' ? (
          <div
            className="prose prose-sm dark:prose-invert max-w-none"
            dangerouslySetInnerHTML={{ __html: message.content }}
          />
        ) : (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        )}
      </div>
    </div>
  );
});
