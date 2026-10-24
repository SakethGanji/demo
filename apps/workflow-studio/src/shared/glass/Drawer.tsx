import { cx } from './cx'

export interface DrawerTab<T extends string> {
  id: T
  label: React.ReactNode
  /** Small mono count after the label; `tone: 'bad'` draws it red (problems, failures). */
  count?: number
  tone?: 'bad'
}

export interface DrawerProps<T extends string> {
  open: boolean
  onOpenChange: (open: boolean) => void
  tabs: DrawerTab<T>[]
  tab: T
  onTabChange: (tab: T) => void
  /** Items in the collapsed header, before the tabs (e.g. the last run's status). */
  lead?: React.ReactNode
  /** Items at the right of the header, before the toggle. */
  trailing?: React.ReactNode
  /** Body height when open. */
  height?: number | string
  className?: string
  'data-testid'?: string
  children?: React.ReactNode
}

/** The bottom drawer: a collapsed header with tabs (Runs · Logs · Problems · Tests); click a tab to open. */
export function Drawer<T extends string>({ open, onOpenChange, tabs, tab, onTabChange, lead, trailing, height = 280, className, children, ...rest }: DrawerProps<T>) {
  const panelId = `gl-drawer-${String(tab)}`
  return (
    <div className={cx('drawer', className)} data-open={open ? '' : undefined} data-testid={rest['data-testid']}>
      <div className="gl-drawer-head">
        {lead}
        <div role="tablist" aria-label="Drawer" className="flex items-center gap-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={open && t.id === tab}
              aria-controls={open && t.id === tab ? panelId : undefined}
              className="gl-drawer-tab"
              onClick={() => {
                if (open && t.id === tab) onOpenChange(false)
                else {
                  onTabChange(t.id)
                  if (!open) onOpenChange(true)
                }
              }}
            >
              {t.label}
              {t.count !== undefined && (
                <span className="gl-count" data-tone={t.tone}>
                  {t.count}
                </span>
              )}
            </button>
          ))}
        </div>
        <span className="gl-sp" />
        {trailing}
        <button type="button" className="gl-icon-btn" aria-label={open ? 'Collapse drawer' : 'Expand drawer'} aria-expanded={open} onClick={() => onOpenChange(!open)}>
          <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden style={{ transform: open ? 'rotate(180deg)' : undefined }}>
            <path d="M3 7.5 6 4.5l3 3" />
          </svg>
        </button>
      </div>
      {open && (
        <div id={panelId} role="tabpanel" className="gl-drawer-body" style={{ height }}>
          {children}
        </div>
      )}
    </div>
  )
}
