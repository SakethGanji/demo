import { useState } from 'react'
import { Popover } from '@base-ui/react/popover'
import { cx } from './cx'

export interface ValueTokenProps {
  /** The current value, rendered inline with a dotted underline. Empty → the placeholder slot. */
  value: string | number | null | undefined
  /** What the value is (`threshold`, `table`), shown in the editor popover and as the tooltip. */
  label?: string
  /** The config path the value comes from (`parameters.limit`), for the caller; set as data-path. */
  path?: string
  /** Called with the new text when the user applies an edit. Without it the token is read-only. */
  onCommit?: (next: string) => void
  /** Marks a value edited since the last run (violet). */
  changed?: boolean
  /** Shown when the value is empty. */
  placeholder?: string
  /** Hint under the input, e.g. "Rerun from here after changing". */
  hint?: React.ReactNode
  inputType?: 'text' | 'number'
  className?: string
  /** Tooltip; defaults to the label (e.g. the raw `{{cells.c2.rows}}` behind a human reference label). */
  title?: string
}

/** A dotted value inside a sentence. Click to edit it in a small glass popover. */
export function ValueToken({ value, label, path, onCommit, changed, placeholder = 'set…', hint, inputType = 'text', className, title }: ValueTokenProps) {
  const text = value === null || value === undefined || value === '' ? '' : String(value)
  const empty = text === ''
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(text)
  const common = {
    'data-path': path,
    'data-changed': changed ? '' : undefined,
    'data-empty': empty ? '' : undefined,
    title: title ?? label,
  }

  if (!onCommit) {
    return (
      <span {...common} data-readonly="" className={cx('vtoken', className)}>
        {empty ? placeholder : text}
      </span>
    )
  }

  const commit = () => {
    setOpen(false)
    if (draft !== text) onCommit(draft)
  }

  return (
    <Popover.Root
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (o) setDraft(text)
      }}
    >
      <Popover.Trigger {...common} className={cx('vtoken', className)} aria-label={label ? `${label}: ${empty ? 'not set' : text}` : undefined}>
        {empty ? placeholder : text}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} align="start" className="z-[70]">
          <Popover.Popup className="gl-overlay" data-glass-token-editor="">
            <form
              className="gl-tokf"
              onSubmit={(e) => {
                e.preventDefault()
                commit()
              }}
            >
              {label && <span className="gl-tokf-label">{label}</span>}
              <input
                autoFocus
                type={inputType}
                value={draft}
                aria-label={label ?? 'Value'}
                onChange={(e) => setDraft(e.target.value)}
              />
              <button type="submit" className="gl-btn" data-variant="primary" data-size="sm">
                Apply
              </button>
            </form>
            {hint && <div className="px-3 pb-2 text-[11.5px] text-faint">{hint}</div>}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}
