import { Sparkles } from 'lucide-react';
import { Callout } from '@/shared/components/ui/surface';
import type { AdaptedFrom } from './appTypes';
import { bannerModel } from './lib/appState';

/**
 * "Adapted from 'Case management app', validated by Payments Ops (21 tests)", then what changed for this request: the old
 * value struck out in muted text, the new one in the foreground. A fact about the pattern, never a score or a fit percentage.
 */
export function AdaptedFromBanner({ adapted }: { adapted: AdaptedFrom | null | undefined }) {
  if (!adapted) return null;
  const m = bannerModel(adapted);
  return (
    <Callout tone="ai" icon={<Sparkles aria-hidden />} className="items-start" data-testid="ai-app-adapted-from">
      <p className="font-medium">
        {m.headline}
        {m.validated && <span className="font-normal">, {m.validated}</span>}
      </p>
      {m.lines.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5" aria-label="What changed for this request">
          {m.lines.map((l) => (
            <li key={l.slot} data-slot={l.slot} title={l.title} className="whitespace-nowrap tabular-nums">
              <s className="text-muted-foreground">{l.from}</s>
              <span className="text-faint" aria-hidden> → </span>
              <span className="sr-only"> changed to </span>
              <span className="font-medium text-foreground">{l.to}</span>
            </li>
          ))}
        </ul>
      )}
    </Callout>
  );
}
