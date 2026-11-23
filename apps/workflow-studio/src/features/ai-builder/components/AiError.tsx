import { AlertTriangle } from 'lucide-react';
import { Callout } from '@/shared/components/ui/surface';
import type { AiUiError } from '../api';

/** A failure in plain English. Never raw engine text, JSON or a stack trace. */
export function AiError({ error, className }: { error: AiUiError | null; className?: string }) {
  if (!error) return null;
  return (
    <Callout
      tone="error"
      icon={<AlertTriangle />}
      className={className}
      role="alert"
      data-testid="ai-error"
      data-code={error.code}
    >
      {error.message}
    </Callout>
  );
}
