/**
 * Segmented control (DESIGN §4, §7): a recessed track and one raised thumb that slides to the pressed
 * segment. Built on Base UI ToggleGroup so it is a real single-select group with roving focus; each
 * segment keeps `data-pressed`.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ToggleGroup } from '@base-ui/react/toggle-group'
import { Toggle } from '@base-ui/react/toggle'
import { cn } from '@/shared/lib/utils'
import { attachLiquidGlass, liquidGlassSupported } from '@/shared/lib/liquid-glass'

export interface SegmentedOption<K extends string> {
  key: K
  label: React.ReactNode
  count?: number
  icon?: React.ReactNode
  disabled?: boolean
  testId?: string
}

/** Measures the pressed child of `track` and returns the thumb's left/width (null before first layout). */
export function useSlidingThumb(track: React.RefObject<HTMLElement | null>, selector: string, dep: unknown) {
  const [box, setBox] = useState<{ left: number; width: number; animate: boolean } | null>(null)
  const first = useRef(true)
  useLayoutEffect(() => {
    const el = track.current
    if (!el) return
    const measure = () => {
      const on = el.querySelector<HTMLElement>(selector)
      if (!on) return setBox(null)
      setBox({ left: on.offsetLeft, width: on.offsetWidth, animate: !first.current })
      first.current = false
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    let raf = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(measure)
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [track, selector, dep])
  return box
}

/**
 * The raised thumb of a recessed track, sliding to `box`. Where liquid glass is supported (Chromium, motion
 * allowed) it sits ABOVE the labels as a clear lens, so the chosen label refracts at the glass rim; elsewhere
 * it is the plain raised fill behind the labels.
 */
export function SlidingThumb({ box }: { box: { left: number; width: number; animate: boolean } | null }) {
  const ref = useRef<HTMLSpanElement>(null)
  const liquid = liquidGlassSupported()
  useEffect(() => {
    const el = ref.current
    if (!liquid || !el || !box) return
    return attachLiquidGlass(el, box.width, el.offsetHeight, { radius: 11, bezel: 5, strength: 9, extra: 'saturate(160%) brightness(1.06)' })
  }, [liquid, box?.width, box])
  if (!box) return null
  return (
    <span
      ref={ref}
      aria-hidden
      data-slot="thumb"
      className={cn(
        'pointer-events-none absolute inset-y-0.5 rounded-full',
        liquid ? 'z-[2] bg-(--thumb-glass) shadow-(--thumb-glass-shadow)' : '-z-10 bg-raise shadow-raise-sm',
        box.animate && 'transition-[left,width] duration-(--d-base) ease-(--ease-io)',
      )}
      style={{ left: box.left, width: box.width }}
    />
  )
}

export function Segmented<K extends string>({
  value,
  onChange,
  options,
  size = 'sm',
  className,
  'aria-label': ariaLabel,
  ...rest
}: {
  value: K
  onChange: (k: K) => void
  options: SegmentedOption<K>[]
  size?: 'xs' | 'sm'
  className?: string
  'aria-label'?: string
  'data-testid'?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const thumb = useSlidingThumb(ref, '[data-pressed]', value)
  return (
    <ToggleGroup
      ref={ref}
      value={[value]}
      onValueChange={(v) => {
        const next = v[0] as K | undefined
        if (next) onChange(next)
      }}
      aria-label={ariaLabel}
      className={cn('relative isolate inline-flex items-center gap-0.5 rounded-full bg-recess p-0.5', className)}
      {...rest}
    >
      <SlidingThumb box={thumb} />
      {options.map((opt) => (
        <Toggle
          key={opt.key}
          value={opt.key}
          disabled={opt.disabled}
          data-testid={opt.testId}
          className={cn(
            'inline-flex items-center gap-1 rounded-full font-medium text-muted-foreground hover:text-foreground disabled:opacity-50 data-[pressed]:text-foreground [&_svg]:size-3.5',
            size === 'xs' ? 'h-6 px-2 text-[11px]' : 'h-6 px-2.5 text-xs',
          )}
        >
          {opt.icon}
          <span>{opt.label}</span>
          {opt.count != null && opt.count > 0 && <span className="text-[11px] tabular-nums opacity-60">{opt.count}</span>}
        </Toggle>
      ))}
    </ToggleGroup>
  )
}
