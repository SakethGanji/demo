import { useState } from 'react';
import { UserRound } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Field } from '@/shared/components/ui/field';
import { MAX_ACTOR_CHARS, setActorName } from '../api';

/**
 * Asked once. Every AI change is recorded under this name until sign-in exists, so the prompt says why it is asked.
 */
export function ActorPrompt({ onSaved }: { onSaved?: (name: string) => void }) {
  const [value, setValue] = useState('');
  const save = () => {
    const clean = setActorName(value);
    if (clean) onSaved?.(clean);
  };
  return (
    <div className="flex flex-col gap-3" data-testid="ai-actor-prompt">
      <div className="flex items-center gap-2 text-[13px] font-medium">
        <UserRound className="size-4 text-muted-foreground" /> Who is asking?
      </div>
      <Field
        label="Your name"
        htmlFor="ai-actor-name"
        help="Every change the AI makes is recorded under this name, so others can see who asked for it."
      >
        <Input
          id="ai-actor-name"
          value={value}
          maxLength={MAX_ACTOR_CHARS}
          placeholder="For example, Asha Patel"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); }}
          data-testid="ai-actor-name-input"
          autoFocus
        />
      </Field>
      <div>
        <Button size="sm" onClick={save} disabled={!value.trim()} data-testid="ai-actor-name-save">Continue</Button>
      </div>
    </div>
  );
}
