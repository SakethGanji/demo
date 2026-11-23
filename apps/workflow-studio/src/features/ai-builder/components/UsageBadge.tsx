import { Cpu } from 'lucide-react';
import { Tip } from '@/shared/components/ui/tooltip';
import type { Proposal } from '../types';

const fmt = (n: number) => n.toLocaleString('en-US');

/** What the AI used for this change: calls, tokens, cost (when prices are set) and which model ran it. */
export function UsageBadge({ proposal }: { proposal: Proposal }) {
  const u = proposal.usage;
  const tokens = u.input_tokens + u.output_tokens;
  const cost = u.cost_usd == null ? null : `$${u.cost_usd.toFixed(u.cost_usd < 0.01 ? 4 : 2)}`;
  const { model, region } = proposal.policy;
  const label = `${model ? `Model ${model}` : 'Model not recorded'}${region ? `, region ${region}` : ''}. Run data is never sent to the model, only its shape.`;
  return (
    <Tip label={label}>
      <span
        className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground"
        data-testid="ai-usage"
        data-calls={u.llm_calls}
        data-tokens={tokens}
      >
        <Cpu className="size-3" />
        {u.llm_calls} AI {u.llm_calls === 1 ? 'call' : 'calls'} · {fmt(tokens)} tokens{cost ? ` · ${cost}` : ''}
      </span>
    </Tip>
  );
}
