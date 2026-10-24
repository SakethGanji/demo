import { useRef } from 'react'
import { cx } from './cx'

export interface ViewSwitchOption<V extends string> {
  value: V
  label: React.ReactNode
  icon?: React.ReactNode
  disabled?: boolean
  title?: string
}

export interface ViewSwitchProps<V extends string> {
  value: V
  onChange: (value: V) => void
  options: ViewSwitchOption<V>[]
  'aria-label': string
  size?: 'sm' | 'md'
  className?: string
}

/** Segmented view switch (Page | Editor | Definition). A radiogroup: arrow keys move the selection. */
export function ViewSwitch<V extends string>({ value, onChange, options, size = 'md', className, ...rest }: ViewSwitchProps<V>) {
  const ref = useRef<HTMLDivElement>(null)
  const enabled = options.filter((o) => !o.disabled)
  const move = (dir: 1 | -1) => {
    const i = enabled.findIndex((o) => o.value === value)
    const next = enabled[(i + dir + enabled.length) % enabled.length]
    if (!next) return
    onChange(next.value)
    ref.current?.querySelector<HTMLButtonElement>(`[data-value="${CSS.escape(next.value)}"]`)?.focus()
  }
  return (
    <div
      ref={ref}
      role="radiogroup"
      aria-label={rest['aria-label']}
      data-size={size === 'sm' ? 'sm' : undefined}
      className={cx('gl-seg', className)}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') (e.preventDefault(), move(1))
        if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') (e.preventDefault(), move(-1))
      }}
    >
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            disabled={o.disabled}
            title={o.title}
            data-value={o.value}
            onClick={() => onChange(o.value)}
          >
            {o.icon}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
