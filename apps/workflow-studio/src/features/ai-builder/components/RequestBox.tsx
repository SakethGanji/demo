import { useState } from 'react';
import { Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Textarea } from '@/shared/components/ui/textarea';
import { Field } from '@/shared/components/ui/field';
import { LiveConcepts } from './LiveConcepts';

export const MAX_REQUEST_CHARS = 8000;

/**
 * Step 1: say what is needed. The AI answers with a plan and tests to check, not with a finished workflow.
 */
export function RequestBox({ projectId, modify, busy, disabled, initialText = '', onSubmit }: {
  /** set: the concepts recognised in the request show as chips under the box */
  projectId?: string;
  /** changing an existing workflow instead of building a new one */
  modify?: boolean;
  busy: boolean;
  disabled?: boolean;
  /** refills the box after a request that failed */
  initialText?: string;
  onSubmit: (request: string) => void;
}) {
  const [text, setText] = useState(initialText);
  const clean = text.trim();
  const tooLong = text.length > MAX_REQUEST_CHARS;
  const submit = () => { if (clean && !tooLong && !busy && !disabled) onSubmit(clean); };
  return (
    <div className="flex flex-col gap-3" data-testid="ai-request-box">
      <Field
        label={modify ? 'What should change?' : 'What should this workflow do?'}
        htmlFor="ai-request"
        error={tooLong ? `That is ${text.length.toLocaleString('en-US')} characters. Please keep it under ${MAX_REQUEST_CHARS.toLocaleString('en-US')}.` : undefined}
        help={modify
          ? 'Describe the change in your own words. You will see the plan and the tests before anything is changed.'
          : 'Say what starts it, what the rules are and who needs to be told. You will review a plan and tests before anything is built.'}
      >
        <Textarea
          id="ai-request"
          value={text}
          rows={modify ? 5 : 7}
          disabled={busy}
          placeholder={modify
            ? 'For example: also email the finance team when a refund is rejected.'
            : 'For example: customers ask for a refund. Refunds over $500 need a manager to approve. The customer is always told the result.'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(); }}
          data-testid="ai-request-input"
        />
        {projectId && <LiveConcepts projectId={projectId} text={text} />}
      </Field>
      <div className="flex items-center gap-2">
        <Button onClick={submit} disabled={!clean || tooLong || busy || disabled} data-testid="ai-request-submit">
          {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
          {busy ? 'Writing the plan…' : 'Write the plan'}
        </Button>
        <span className="text-[12px] text-muted-foreground">Nothing is saved yet.</span>
      </div>
    </div>
  );
}
