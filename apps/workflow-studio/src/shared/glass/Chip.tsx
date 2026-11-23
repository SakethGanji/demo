import { cx, type Tone } from './cx'

export interface ChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
  /** A leading dot in the chip colour; `live` pulses it. */
  dot?: boolean | 'live'
  size?: 'sm' | 'md'
  mono?: boolean
}

const TONE_CLASS: Record<Tone, string | undefined> = {
  neutral: undefined,
  ok: 'chip-ok',
  warn: 'chip-warn',
  prod: 'chip-prod',
  ai: 'chip-ai',
  brand: 'chip-brand',
}

/** A status pill: `v4 · Live`, `writes · PRODUCTION`, `needs AI`. Never clickable (DESIGN-SYSTEM v3 §3): use a button for actions. */
export function Chip({ tone = 'neutral', dot, size = 'md', mono, className, children, ...rest }: ChipProps) {
  return (
    <span className={cx('gl-chip', TONE_CLASS[tone], className)} data-tone={tone} data-size={size === 'sm' ? 'sm' : undefined} data-mono={mono ? '' : undefined} {...rest}>
      {dot && <span className="gl-chip-dot" data-live={dot === 'live' ? '' : undefined} aria-hidden />}
      {children}
    </span>
  )
}
