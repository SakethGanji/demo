import { Menu } from '@base-ui/react/menu'
import { cx } from './cx'

export interface SplitButtonItem {
  id: string
  label: React.ReactNode
  hint?: React.ReactNode
  disabled?: boolean
  onSelect: () => void
}

export interface SplitButtonProps {
  label: React.ReactNode
  icon?: React.ReactNode
  onClick: () => void
  items: SplitButtonItem[]
  variant?: 'primary' | 'default' | 'ghost'
  size?: 'sm' | 'md'
  disabled?: boolean
  /** Accessible name of the ▾ half. */
  menuLabel?: string
  /** Accessible name of the main half, when the visible label is not enough. */
  ariaLabel?: string
  className?: string
  'data-testid'?: string
}

/** Run ▾: a main action plus a menu of variants (Run with payload, Run from step…). */
export function SplitButton({ label, icon, onClick, items, variant = 'primary', size = 'md', disabled, menuLabel = 'More options', ariaLabel, className, ...rest }: SplitButtonProps) {
  const v = variant === 'default' ? undefined : variant
  const s = size === 'sm' ? 'sm' : undefined
  return (
    <div className={cx('gl-split', className)} data-testid={rest['data-testid']}>
      <button type="button" className="gl-btn" data-variant={v} data-size={s} disabled={disabled} onClick={onClick} aria-label={ariaLabel}>
        {icon}
        {label}
      </button>
      <Menu.Root>
        <Menu.Trigger className="gl-btn" data-variant={v} data-size={s} disabled={disabled} aria-label={menuLabel}>
          <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
            <path d="M3 4.5 6 7.5l3-3" />
          </svg>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner sideOffset={6} align="end" className="z-[70]">
            <Menu.Popup className="gl-overlay gl-menu">
              {items.map((it) => (
                <Menu.Item key={it.id} className="gl-menu-item" disabled={it.disabled} onClick={it.onSelect}>
                  <span>{it.label}</span>
                  {it.hint && <span className="gl-hint">{it.hint}</span>}
                </Menu.Item>
              ))}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </div>
  )
}
