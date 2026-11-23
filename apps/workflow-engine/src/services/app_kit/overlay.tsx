/** Studio UI kit — overlays (provided by Studio, refreshed every builder turn; do not edit).
 * Modal (dialog over a dimmed backdrop), ConfirmDialog (yes/no before a destructive action), Popover (floating panel
 * anchored to a trigger) and Menu (a trigger that opens a list of actions). Colours come only from theme tokens. */
import React, { useEffect, useRef } from 'react'
import { cx, Button } from './ui'
import { usePersistedUiState } from './hooks'

type DivProps = Omit<React.HTMLAttributes<HTMLDivElement>, 'title' | 'children'>

const MODAL_W = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl' }

/** Escape closes, a click outside `ref` closes (only while `open`). */
function useDismiss(open: boolean, close: () => void, ref?: React.RefObject<HTMLElement>) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    const onDown = (e: MouseEvent) => { if (ref?.current && !ref.current.contains(e.target as Node)) close() }
    window.addEventListener('keydown', onKey)
    if (ref) document.addEventListener('mousedown', onDown)
    return () => { window.removeEventListener('keydown', onKey); if (ref) document.removeEventListener('mousedown', onDown) }
  }, [open, close, ref])
}

/** Open state that is controlled (`open` given) or internal (a `trigger` opens it); the internal one survives Studio preview edits. */
function useOpen(key: string, open: boolean | undefined, defaultOpen: boolean, onOpenChange?: (o: boolean) => void): [boolean, (o: boolean) => void] {
  const [inner, setInner] = usePersistedUiState<boolean>(open === undefined ? key : null, defaultOpen)
  const value = open ?? inner
  const set = (o: boolean) => { if (open === undefined) setInner(o); onOpenChange?.(o) }
  return [value, set]
}

/** A dialog over a dimmed backdrop: title, optional description, body and a footer of buttons. Controlled with `open` +
 * `onOpenChange`, or give a `trigger` (e.g. a Button) and it opens itself. Escape or a backdrop click closes it.
 * @category overlay
 * @example <Modal title="New request" trigger={<Button variant="primary">New</Button>} footer={<Button variant="primary">Save</Button>}>Form fields</Modal> */
export function Modal({ open, defaultOpen = false, onOpenChange, trigger, title, description, footer, size = 'md', closeOnBackdrop = true, stateKey, className, headerClassName, bodyClassName, footerClassName, children, ...rest }: DivProps & {
  /** Controlled open state (omit to let `trigger` open it).
   * @control hidden @group Data */
  open?: boolean
  /** Open on first render when uncontrolled.
   * @control boolean @group Behavior @default false */
  defaultOpen?: boolean
  /** Called with the new open state (close button, Escape, backdrop, trigger).
   * @control hidden @group Behavior */
  onOpenChange?: (open: boolean) => void
  /** Element that opens the modal when clicked.
   * @control slot @group Content */
  trigger?: React.ReactNode
  /** Heading of the dialog.
   * @control text @group Content */
  title?: React.ReactNode
  /** One line under the title.
   * @control text @group Content */
  description?: React.ReactNode
  /** Buttons at the bottom right.
   * @control slot @group Content */
  footer?: React.ReactNode
  /** Maximum width.
   * @control select @options sm|md|lg|xl @group Layout @default md */
  size?: keyof typeof MODAL_W
  /** Close when the dimmed backdrop is clicked.
   * @control boolean @group Behavior @default true */
  closeOnBackdrop?: boolean
  /** Id under which the Studio preview remembers an uncontrolled modal's open state across edits.
   * @control hidden @group Behavior */
  stateKey?: string
  /** Dialog body.
   * @control slot @group Content */
  children?: React.ReactNode
  /** Classes for the dialog panel (the dimmed backdrop is not styleable).
   * @control hidden @group Style */
  className?: string
  headerClassName?: string
  bodyClassName?: string
  footerClassName?: string
}) {
  const [isOpen, setOpen] = useOpen(stateKey ?? `Modal:${typeof title === 'string' ? title : ''}`, open, defaultOpen, onOpenChange)
  const close = () => setOpen(false)
  useDismiss(isOpen, close)
  return (
    <>
      {trigger != null && <span className="inline-flex" onClick={() => setOpen(true)}>{trigger}</span>}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" data-testid="modal">
          <div className="absolute inset-0 bg-overlay" aria-hidden onClick={closeOnBackdrop ? close : undefined} />
          <div role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined} data-vcomp="Modal" {...rest}
            className={cx('relative flex max-h-full w-full flex-col overflow-hidden rounded-xl border border-border bg-card text-card-foreground shadow-2xl', MODAL_W[size], className)}>
            {(title || description) && (
              <header data-vpart="header" className={cx('flex items-start gap-2 border-b border-border px-5 py-4', headerClassName)}>
                <div className="min-w-0 flex-1">
                  {title && <h3 className="m-0 text-base font-semibold">{title}</h3>}
                  {description && <p className="m-0 mt-1 text-sm opacity-65">{description}</p>}
                </div>
                <button type="button" onClick={close} aria-label="Close" className="rounded-md px-2 py-1 opacity-65 hover:bg-muted">✕</button>
              </header>
            )}
            <div data-vpart="body" className={cx('min-h-0 flex-1 overflow-y-auto px-5 py-4 text-sm', bodyClassName)}>{children}</div>
            {footer && <footer data-vpart="footer" className={cx('flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3', footerClassName)}>{footer}</footer>}
          </div>
        </div>
      )}
    </>
  )
}

/** Ask before an irreversible action: a message plus Cancel and a confirm button (red for `tone="danger"`).
 * @category overlay
 * @example <ConfirmDialog open={confirming} title="Delete request?" message="This cannot be undone." confirmLabel="Delete" tone="danger" onConfirm={remove} onCancel={() => setConfirming(false)} /> */
export function ConfirmDialog({ open, title = 'Are you sure?', message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', tone = 'primary', loading = false, onConfirm, onCancel, className, ...rest }: DivProps & {
  /** Whether the dialog is showing.
   * @control hidden @group Data */
  open: boolean
  /** The question.
   * @control text @group Content @default Are you sure? */
  title?: React.ReactNode
  /** What will happen.
   * @control textarea @group Content */
  message?: React.ReactNode
  /** Label of the confirm button.
   * @control text @group Content @default Confirm */
  confirmLabel?: React.ReactNode
  /** Label of the cancel button.
   * @control text @group Content @default Cancel */
  cancelLabel?: React.ReactNode
  /** Colour of the confirm button.
   * @control select @options primary|danger @group Style @default primary */
  tone?: 'primary' | 'danger'
  /** Show a spinner on the confirm button and disable it.
   * @control boolean @group Behavior @default false */
  loading?: boolean
  /** Called when confirmed.
   * @control hidden @group Behavior */
  onConfirm: () => void
  /** Called on Cancel, Escape or a backdrop click.
   * @control hidden @group Behavior */
  onCancel: () => void
}) {
  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onCancel() }} title={title} size="sm" className={className} data-vcomp="ConfirmDialog" {...rest}
      footer={<><Button onClick={onCancel}>{cancelLabel}</Button><Button variant={tone === 'danger' ? 'danger' : 'primary'} loading={loading} onClick={onConfirm}>{confirmLabel}</Button></>}>
      {message}
    </Modal>
  )
}

const PLACE = { 'bottom-start': 'left-0 top-full mt-1', 'bottom-end': 'right-0 top-full mt-1', 'top-start': 'bottom-full left-0 mb-1', 'top-end': 'bottom-full right-0 mb-1' }

/** A small floating panel anchored to its trigger; a click outside or Escape closes it.
 * @category overlay
 * @example <Popover trigger={<Button>Filters</Button>}>Filter controls</Popover> */
export function Popover({ trigger, open, onOpenChange, placement = 'bottom-start', className, children, ...rest }: DivProps & {
  /** Element that toggles the panel.
   * @control slot @group Content */
  trigger: React.ReactNode
  /** Controlled open state (omit for internal).
   * @control hidden @group Data */
  open?: boolean
  /** Called with the new open state.
   * @control hidden @group Behavior */
  onOpenChange?: (open: boolean) => void
  /** Where the panel opens relative to the trigger.
   * @control select @options bottom-start|bottom-end|top-start|top-end @group Layout @default bottom-start */
  placement?: keyof typeof PLACE
  /** Panel content.
   * @control slot @group Content */
  children?: React.ReactNode
}) {
  const [isOpen, setOpen] = useOpen('', open, false, onOpenChange)
  const ref = useRef<HTMLSpanElement>(null)
  useDismiss(isOpen, () => setOpen(false), ref)
  return (
    <span ref={ref} className="relative inline-flex">
      <span className="inline-flex" onClick={() => setOpen(!isOpen)}>{trigger}</span>
      {isOpen && (
        <div role="dialog" data-vcomp="Popover" {...rest}
          className={cx('absolute z-40 min-w-[12rem] rounded-lg border border-border bg-card p-3 text-sm text-card-foreground shadow-lg', PLACE[placement], className)}>
          {children}
        </div>
      )}
    </span>
  )
}

export interface MenuItem {
  id: string
  /** @control text */
  label: React.ReactNode
  onSelect?: () => void
  /** red text, for destructive actions */
  danger?: boolean
  disabled?: boolean
  /** a short glyph or icon element before the label */
  icon?: React.ReactNode
}

/** A trigger (default: a "⋯" button) that opens a list of actions.
 * @category navigation
 * @example <Menu items={[{ id: 'edit', label: 'Edit', onSelect: edit }, { id: 'delete', label: 'Delete', danger: true, onSelect: remove }]} /> */
export function Menu({ items, trigger, align = 'end', className, ...rest }: DivProps & {
  /** The actions, top to bottom.
   * @control hidden @group Data */
  items: MenuItem[]
  /** Element that opens the menu (default: a ⋯ button).
   * @control slot @group Content */
  trigger?: React.ReactNode
  /** Which edge of the trigger the list lines up with.
   * @control select @options start|end @group Layout @default end */
  align?: 'start' | 'end'
}) {
  const [open, setOpen] = useOpen('', undefined, false)
  return (
    <Popover open={open} onOpenChange={setOpen} placement={align === 'end' ? 'bottom-end' : 'bottom-start'} className={cx('p-1', className)} data-vcomp="Menu" {...rest}
      trigger={trigger ?? <button type="button" aria-label="More actions" className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted">⋯</button>}>
      <ul role="menu" className="m-0 flex list-none flex-col p-0">
        {items.map((it, i) => (
          <li key={it.id} role="none">
            <button type="button" role="menuitem" disabled={it.disabled} data-vpart={`items.${i}`}
              onClick={() => { setOpen(false); it.onSelect?.() }}
              className={cx('flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50',
                it.danger ? 'text-danger' : 'text-foreground')}>
              {it.icon != null && <span aria-hidden className="w-4 text-center text-muted-foreground">{it.icon}</span>}
              {it.label}
            </button>
          </li>
        ))}
      </ul>
    </Popover>
  )
}
