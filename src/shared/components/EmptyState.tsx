import type { ReactNode } from 'react'
import { PluckString } from '@/shared/primitives/PluckString'

/** The first-run / nothing-yet panel: a string at rest (nothing is ringing yet), what will appear here, and the
 *  actions that make it appear. Used wherever a page has no runs, versions or results to show. */
export function EmptyState({ title, children, actions, testId }: { title: string; children?: ReactNode; actions?: ReactNode; testId?: string }) {
  return (
    <div className="card empty-state" data-testid={testId}>
      <div className="es-string" aria-hidden><PluckString pluck={1} height={22} /></div>
      <b className="es-title">{title}</b>
      {children && <div className="es-body">{children}</div>}
      {actions && <div className="es-actions">{actions}</div>}
    </div>
  )
}
