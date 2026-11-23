import { useState } from 'react';
import { Check } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { cn } from '@/shared/lib/utils';
import type { UnderstandingQuestion } from '../types';
import {
  CRON_PRESETS, DURATION_SUGGESTIONS, NEW_ROLE, controlFor, hasAnswer, humanise,
} from '../lib/understanding';

/** One option, as a radio-like chip. Recessed when idle, AI-soft when picked: the fill carries the state, never a border. */
function OptionChip({ label, picked, onPick, multi, ...rest }: {
  label: string; picked: boolean; onPick: () => void; multi?: boolean; 'data-testid'?: string; 'data-autofocus'?: boolean;
}) {
  return (
    <button
      type="button"
      role={multi ? 'checkbox' : 'radio'}
      aria-checked={picked}
      onClick={onPick}
      data-picked={picked ? 'true' : 'false'}
      className={cn(
        'inline-flex h-7 items-center gap-1 rounded-ctl px-2.5 text-[12.5px] outline-none transition-colors duration-(--d-fast) focus-visible:shadow-focus',
        picked ? 'bg-(--ai-soft) font-medium text-ai-text' : 'bg-recess text-foreground hover:bg-(--hover-fill)',
      )}
      {...rest}
    >
      {picked && multi && <Check className="size-3" aria-hidden />}
      {label}
    </button>
  );
}

/**
 * The control for one question (PLAN-CAPGRAPH-UI §3.4): chips for short lists, a menu for long ones, a text box for free
 * answers. `onCommit` settles the question; typing alone only changes the draft.
 */
export function QuestionControl({ q, value, onDraft, onCommit, roles, invalid, hint }: {
  q: UnderstandingQuestion;
  value: unknown;
  onDraft: (v: unknown) => void;
  /** settle the question with this value */
  onCommit: (v: unknown) => void;
  /** role questions: the roles to offer, best first */
  roles: Array<{ key: string; label: string }>;
  invalid?: boolean;
  /** a fact under the control ("Used for Escalation in 2 apps") */
  hint?: string | null;
}) {
  const kind = controlFor(q);
  const [other, setOther] = useState(false);
  const id = q.id;
  const text = typeof value === 'string' ? value : '';
  const list = Array.isArray(value) ? (value as string[]) : [];
  const toggle = (opt: string) => onDraft(list.includes(opt) ? list.filter((x) => x !== opt) : [...list, opt]);
  const commitText = () => { if (hasAnswer(text)) onCommit(text); };

  const textInput = (placeholder: string, autoFocus = false) => (
    <Input
      value={text}
      placeholder={placeholder}
      aria-label={q.text}
      aria-invalid={invalid || undefined}
      onChange={(e) => onDraft(e.target.value)}
      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitText(); } }}
      onBlur={commitText}
      autoFocus={autoFocus}
      data-autofocus
      data-testid={`ai-question-input-${id}`}
    />
  );

  const doneButton = (n: number) => (
    <div>
      <Button size="xs" variant="outline" disabled={n === 0} onClick={() => onCommit(list)} data-testid={`ai-question-done-${id}`}>Done</Button>
    </div>
  );

  let control: React.ReactNode;
  switch (kind) {
    case 'role': {
      const labels: Record<string, string> = { ...Object.fromEntries(roles.map((r) => [r.key, r.label])), [NEW_ROLE]: 'New role…' };
      const picked = typeof value === 'string' && labels[value] ? value : value && typeof value === 'object' ? NEW_ROLE : null;
      control = (
        <div className="flex flex-col gap-1.5">
          <Select
            value={picked}
            items={labels}
            onValueChange={(v) => {
              if (v === NEW_ROLE) { setOther(true); onDraft({ new_role: '' }); return; }
              setOther(false);
              if (typeof v === 'string') onCommit(v);
            }}
          >
            <SelectTrigger className="w-full" aria-invalid={invalid || undefined} aria-label={q.text} data-autofocus data-testid={`ai-question-select-${id}`}>
              <SelectValue placeholder="Select a role" />
            </SelectTrigger>
            <SelectContent>
              {roles.map((r) => <SelectItem key={r.key} value={r.key} data-testid={`ai-question-option-${id}-${r.key.replace(/[^a-z0-9]+/gi, '-')}`}>{r.label}</SelectItem>)}
              <SelectItem value={NEW_ROLE} data-testid={`ai-question-option-${id}-new`}>New role…</SelectItem>
            </SelectContent>
          </Select>
          {(other || picked === NEW_ROLE) && (
            <Input
              value={typeof value === 'object' && value && 'new_role' in value ? String((value as { new_role: unknown }).new_role ?? '') : ''}
              placeholder="Name the new role"
              aria-label="New role name"
              aria-invalid={invalid || undefined}
              autoFocus
              onChange={(e) => onDraft({ new_role: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); if (hasAnswer(value)) onCommit(value); } }}
              onBlur={() => { if (hasAnswer(value)) onCommit(value); }}
              data-testid={`ai-question-input-${id}`}
            />
          )}
        </div>
      );
      break;
    }
    case 'role-multi':
      control = (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={q.text}>
            {roles.map((r, i) => (
              <OptionChip key={r.key} label={r.label} multi picked={list.includes(r.key)} onPick={() => toggle(r.key)} data-autofocus={i === 0 || undefined}
                data-testid={`ai-question-option-${id}-${r.key.replace(/[^a-z0-9]+/gi, '-')}`} />
            ))}
          </div>
          {doneButton(list.length)}
        </div>
      );
      break;
    case 'duration':
      control = (
        <div className="flex flex-col gap-1.5">
          {textInput('48h')}
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Suggestions">
            {DURATION_SUGGESTIONS.map((s) => (
              <OptionChip key={s} label={s} picked={text.trim() === s} onPick={() => onCommit(s)} data-testid={`ai-question-option-${id}-${s.replace(/\s+/g, '-')}`} />
            ))}
          </div>
        </div>
      );
      break;
    case 'cron': {
      const preset = CRON_PRESETS.find((p) => p.value === text);
      control = (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={q.text}>
            {CRON_PRESETS.map((p, i) => (
              <OptionChip key={p.value} label={p.label} picked={text === p.value} onPick={() => { setOther(false); onCommit(p.value); }} data-autofocus={i === 0 || undefined}
                data-testid={`ai-question-option-${id}-${p.value.replace(/[^a-z0-9]+/gi, '-')}`} />
            ))}
            <OptionChip label="Custom…" picked={other || (!!text && !preset)} onPick={() => { setOther(true); onDraft(''); }} data-testid={`ai-question-option-${id}-custom`} />
          </div>
          {(other || (!!text && !preset)) && textInput('daily, or 0 8 * * 1', true)}
        </div>
      );
      break;
    }
    case 'chips': {
      const withOther = q.kind === 'field';
      control = (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={q.text}>
            {q.options.map((o, i) => (
              <OptionChip key={o} label={q.option_labels?.[o] ?? o} picked={value === o && !other} onPick={() => { setOther(false); onCommit(o); }} data-autofocus={i === 0 || undefined}
                data-testid={`ai-question-option-${id}-${o.replace(/[^a-z0-9]+/gi, '-')}`} />
            ))}
            {withOther && <OptionChip label="Other…" picked={other} onPick={() => { setOther(true); onDraft(''); }} data-testid={`ai-question-option-${id}-other`} />}
          </div>
          {other && textInput(humanise(q.slot.replaceAll('_', ' ')), true)}
        </div>
      );
      break;
    }
    case 'chips-multi':
      control = (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={q.text}>
            {q.options.map((o, i) => (
              <OptionChip key={o} label={q.option_labels?.[o] ?? o} multi picked={list.includes(o)} onPick={() => toggle(o)} data-autofocus={i === 0 || undefined}
                data-testid={`ai-question-option-${id}-${o.replace(/[^a-z0-9]+/gi, '-')}`} />
            ))}
          </div>
          {doneButton(list.length)}
        </div>
      );
      break;
    case 'select': {
      const labels = Object.fromEntries(q.options.map((o) => [o, q.option_labels?.[o] ?? o]));
      control = (
        <Select value={typeof value === 'string' && labels[value] ? value : null} items={labels} onValueChange={(v) => { if (typeof v === 'string') onCommit(v); }}>
          <SelectTrigger className="w-full" aria-invalid={invalid || undefined} aria-label={q.text} data-autofocus data-testid={`ai-question-select-${id}`}>
            <SelectValue placeholder="Select one" />
          </SelectTrigger>
          <SelectContent>
            {q.options.map((o) => <SelectItem key={o} value={o} data-testid={`ai-question-option-${id}-${o.replace(/[^a-z0-9]+/gi, '-')}`}>{labels[o]}</SelectItem>)}
          </SelectContent>
        </Select>
      );
      break;
    }
    default:
      control = textInput('Type your answer');
  }

  return (
    <div className="flex flex-col gap-1.5">
      {control}
      {hint && <div className="text-[12px] leading-4 text-muted-foreground" data-testid={`ai-question-hint-${id}`}>{hint}</div>}
    </div>
  );
}
