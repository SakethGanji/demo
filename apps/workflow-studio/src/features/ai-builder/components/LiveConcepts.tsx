import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { ConceptChip } from '@/features/knowledge/kit/ConceptChip';
import { Ripple } from '@/features/knowledge/kit/Construct';
import { kg } from '@/features/knowledge/kit/beats';
import { motionOff } from '@/shared/motion/testFlag';
import { dur, ease } from '@/shared/motion/tokens';
import { aiBuilderApi } from '../api';
import { chipTone, liveChips, slugOf } from '../lib/understanding';

/**
 * "I hear ...": the concepts the system recognises while the request is typed (PLAN-CAPGRAPH-UI §3.2). Lexical only, so it
 * costs no model call and saves nothing. It is decoration: a failed or slow lookup just leaves the last chips where they are.
 */
export function LiveConcepts({ projectId, text }: { projectId: string; text: string }) {
  const [debounced, setDebounced] = useState(text);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(text), motionOff() ? 0 : kg.chips.debounceMs);
    return () => window.clearTimeout(t);
  }, [text]);

  const wanted = debounced.trim();
  const enabled = wanted.length >= kg.chips.minChars;
  const q = useQuery({
    queryKey: ['kg-live', projectId, wanted],
    queryFn: ({ signal }) => aiBuilderApi.understandLive(projectId, wanted, signal),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    retry: false,
  });

  const { shown, more } = useMemo(() => liveChips(q.data?.concepts, kg.chips.max), [q.data]);
  const visible = enabled && shown.length > 0;

  // "it heard you": one ripple on the sparkle each time a result brings a chip that was not there before
  const keyList = shown.map((c) => c.key).join('|');
  const [seenKeys, setSeenKeys] = useState('');
  const [tick, setTick] = useState(0);
  if (keyList !== seenKeys) {
    setSeenKeys(keyList);
    const before = new Set(seenKeys.split('|'));
    if (keyList && keyList.split('|').some((k) => !before.has(k))) setTick((n) => n + 1);
  }

  return (
    <AnimatePresence initial={false}>
      {visible && (
        <motion.div
          key="live"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: dur.base, ease: ease.out }}
          className="overflow-hidden"
          data-testid="ai-live-concepts"
          data-count={shown.length}
        >
          <div className="flex flex-wrap items-center gap-1 pt-0.5">
            <span className="relative mr-0.5 inline-flex items-center gap-1 text-[12px] text-ai-text">
              <Sparkles className="size-3" aria-hidden />
              I hear
              {tick > 0 && (
                <svg className="pointer-events-none absolute left-0 top-0 size-3 overflow-visible" aria-hidden>
                  <Ripple key={tick} x={6} y={6} />
                </svg>
              )}
            </span>
            <AnimatePresence initial={false} mode="popLayout">
              {shown.map((c) => (
                <motion.span key={c.key} layout exit={{ opacity: 0, transition: { duration: dur.fast, ease: ease.in } }} className="inline-flex">
                  <ConceptChip
                    conceptKey={c.key}
                    label={c.label}
                    tone={chipTone(c)}
                    hint={c.evidence ? `matched ${c.evidence}` : undefined}
                    data-testid={`ai-live-concept-${slugOf(c.key)}`}
                  />
                </motion.span>
              ))}
            </AnimatePresence>
            {more > 0 && <span className="text-[12px] text-muted-foreground" data-testid="ai-live-more">+{more}</span>}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
