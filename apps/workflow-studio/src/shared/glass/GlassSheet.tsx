import { Dialog } from '@base-ui/react/dialog'
import { cx } from './cx'

export interface GlassSheetPane {
  title?: React.ReactNode
  content: React.ReactNode
}

export interface GlassSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** `right` side sheet · `bottom` sheet · `wide` centred sheet (use `panes` for Input | Setup | Output). */
  side?: 'right' | 'bottom' | 'wide'
  title: React.ReactNode
  /** Header items after the title (chips, buttons). */
  actions?: React.ReactNode
  /** Three panes for the wide sheet. Ignored when children are given. */
  panes?: [GlassSheetPane, GlassSheetPane, GlassSheetPane]
  /** Modal sheets dim the page; non-modal ones keep it interactive. Default true. */
  modal?: boolean
  className?: string
  'data-testid'?: string
  children?: React.ReactNode
}

/** A glass sheet over Base UI Dialog (focus trap, Esc to close, portal). */
export function GlassSheet({ open, onOpenChange, side = 'right', title, actions, panes, modal = true, className, children, ...rest }: GlassSheetProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={modal}>
      <Dialog.Portal>
        {modal && <Dialog.Backdrop className="gl-scrim" />}
        <Dialog.Popup className={cx('gl-overlay gl-sheet', className)} data-side={side} data-testid={rest['data-testid']}>
          <div className="gl-sheet-head">
            <Dialog.Title render={<h2 />}>{title}</Dialog.Title>
            <span className="flex-1" />
            {actions}
            <Dialog.Close className="gl-icon-btn" aria-label="Close">
              <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden>
                <path d="M3 3l6 6M9 3 3 9" />
              </svg>
            </Dialog.Close>
          </div>
          <div className="gl-sheet-body">
            {children ??
              (panes && (
                <div className="sheet-3pane h-full">
                  {panes.map((p, i) => (
                    <section key={i} className="gl-pane" aria-label={typeof p.title === 'string' ? p.title : undefined}>
                      {p.title && <div className="gl-pane-title">{p.title}</div>}
                      {p.content}
                    </section>
                  ))}
                </div>
              ))}
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
