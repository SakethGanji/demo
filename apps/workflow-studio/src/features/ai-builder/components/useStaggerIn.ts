import { useLayoutEffect, type RefObject } from 'react';
import { animate, stagger } from 'motion/react';
import { dur, ease, stagger as tokens } from '@/shared/motion/tokens';

/** Fades and lifts the elements `pick` finds under `ref`, one after another (`stagger.list`), whenever `key` changes. */
export function useStaggerIn(ref: RefObject<HTMLElement | null>, pick: (root: HTMLElement) => Element[], key: unknown) {
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const els = pick(root);
    if (!els.length) return;
    const run = animate(els, { opacity: [0, 1], y: [8, 0] }, { delay: stagger(tokens.list), duration: dur.base, ease: ease.out });
    return () => run.complete();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the identity of the content; `pick` is a pure selector
  }, [key]);
}
