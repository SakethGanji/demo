import { useEffect, useRef } from 'react';
import { animate } from 'motion/react';
import { typedSlice } from '@/features/workflow-editor/components/canvas/stageGeometry';
import { motionOff } from '@/shared/motion/testFlag';

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/**
 * Text that types itself once (18 ms a character, capped) and then is plain text. An invisible copy holds the final
 * size, so the line never changes height while it types, and the typing writes through a ref, never React state.
 */
export function TypedText({ text, active = true, delay = 0, perChar = 0.018, cap = 0.2, className, ...rest }: {
  text: string;
  /** false = show it whole at once */
  active?: boolean;
  delay?: number;
  perChar?: number;
  cap?: number;
  className?: string;
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'>) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!active || motionOff() || reduced() || !text) {
      el.textContent = text;
      return;
    }
    el.textContent = '';
    const run = animate(0, 1, {
      delay, duration: Math.min(cap, text.length * perChar), ease: 'linear',
      onUpdate: (p) => { el.textContent = typedSlice(text, p); },
      onComplete: () => { el.textContent = text; },
    });
    return () => { run.stop(); el.textContent = text; };
  }, [text, active, delay, perChar, cap]);
  return (
    <span className={`relative inline-block ${className ?? ''}`} {...rest}>
      <span className="invisible" aria-hidden>{text}</span>
      <span ref={ref} className="absolute inset-0" />
    </span>
  );
}
