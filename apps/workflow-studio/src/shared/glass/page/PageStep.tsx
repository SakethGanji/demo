import { useState } from 'react'
import { cx, type StepStatus } from '../cx'
import { StatusMark } from '../StatusMark'
import { KindLabel } from '../KindLabel'

export interface PageStepProps {
  /** Mark in the rail gutter. */
  status: StepStatus
  /** Machine kind in the mono label (`query`, `http`, `if`, `browser`). */
  kind: string
  /** Step / cell name, after the kind in the label row. */
  label?: React.ReactNode
  /** The step as a sentence. Put `<ValueToken>`s inside it. */
  sentence: React.ReactNode
  /** Inline result under the sentence (use `<StepResult>` pills, a chip, a short summary). */
  result?: React.ReactNode
  /** One annotation line: who · as whom · via; ms. Truncates to one line; click toggles it open. */
  annotation?: React.ReactNode
  /** Actions slot, right of the label row (Rerun, ⋯). Hidden until hover unless `actionsAlways`. */
  actions?: React.ReactNode
  actionsAlways?: boolean
  /** Result is from an earlier run: dims the body. A string adds the note, e.g. "from run 1". */
  stale?: boolean | string
  /** Off the taken path / not relevant: muted sentence and tokens. */
  dim?: boolean
  /** The rail segment below this step is on the taken run path (green). */
  onPath?: boolean
  /** Small mono text under the mark (step number). */
  num?: React.ReactNode
  /** Extra mono parts after the kind (`bank-prod`, `GET`). */
  kindParts?: React.ReactNode[]
  /** Right-aligned mono meta in the label row (`120 ms`, `09:42`). */
  meta?: React.ReactNode
  /** Badges by exception, after the label (e.g. a `needs AI` or `writes · PRODUCTION` chip). */
  badges?: React.ReactNode
  selected?: boolean
  /** What the workspace is showing: a 3px primary inset edge. */
  linked?: boolean
  /** Play the one-shot highlight (e.g. after jumping to this step). Change the key to replay. */
  flash?: boolean
  /** Click on the step body (not on its buttons / links / inputs). */
  onSelect?: () => void
  /** Hide the rail segment below (the last step of a rail hides it automatically). */
  last?: boolean
  id?: string
  className?: string
  'data-testid'?: string
  /** Extra body under the annotation: a table preview, a diff, a nested group. */
  children?: React.ReactNode
}

const INTERACTIVE = 'button, a, input, textarea, select, [role="button"], [role="option"], [contenteditable="true"]'

/**
 * One step on a page: rail gutter (status mark + rail line) · kind label · sentence · inline result ·
 * annotation · actions. Used by the editor's Page view and by Notebook cells. Place steps as
 * siblings inside a `PageSection` (or `PageRail`) so the rail joins up.
 */
export function PageStep(props: PageStepProps) {
  const { status, kind, label, sentence, result, annotation, actions, actionsAlways, stale, dim, onPath, num, kindParts, meta, badges, selected, linked, flash, onSelect, last, id, className, children } = props
  const [annOpen, setAnnOpen] = useState(false)
  return (
    <div
      id={id}
      data-testid={props['data-testid']}
      data-glass-step=""
      data-status={status}
      data-kind={kind}
      data-path={onPath ? '' : undefined}
      data-stale={stale ? '' : undefined}
      data-dim={dim ? '' : undefined}
      data-selected={selected ? '' : undefined}
      data-linked={linked ? '' : undefined}
      data-flash={flash ? '' : undefined}
      data-clickable={onSelect ? '' : undefined}
      data-last={last ? '' : undefined}
      className={cx('gl-step', className)}
    >
      <div className="gl-step-gutter">
        <StatusMark status={status} />
        {num !== undefined && <span className="gl-step-num">{num}</span>}
      </div>
      <div
        className="gl-step-body"
        onClick={
          onSelect
            ? (e) => {
                if ((e.target as Element).closest(INTERACTIVE)) return
                onSelect()
              }
            : undefined
        }
      >
        <div className="kind-label gl-step-head">
          <KindLabel kind={kind} parts={kindParts} />
          {label !== undefined && label !== null && label !== '' && (
            <>
              <span aria-hidden className="gl-sep">·</span>
              <span className="gl-step-name">{label}</span>
            </>
          )}
          {typeof stale === 'string' && <span className="gl-stale-note">{stale}</span>}
          {badges}
          <span className="gl-sp" />
          {meta !== undefined && <span className="gl-step-meta">{meta}</span>}
          {actions && (
            <span className="gl-step-actions" data-hover={actionsAlways ? undefined : ''}>
              {actions}
            </span>
          )}
        </div>
        <p className="gl-step-sentence">{sentence}</p>
        {result && <div className="gl-step-result">{result}</div>}
        {annotation && (
          <button type="button" className="gl-step-ann" data-open={annOpen ? '' : undefined} aria-expanded={annOpen} onClick={() => setAnnOpen((o) => !o)}>
            <span>{annotation}</span>
          </button>
        )}
        {children && <div className="gl-step-extra">{children}</div>}
      </div>
    </div>
  )
}

export interface StepResultProps {
  tone?: 'neutral' | 'ok' | 'warn' | 'bad'
  children: React.ReactNode
  className?: string
}

/** The small mono result pill under a sentence: `3 rows`, `200 OK · 84 ms`. */
export function StepResult({ tone = 'neutral', children, className }: StepResultProps) {
  return (
    <span className={cx('gl-res', className)} data-tone={tone === 'neutral' ? undefined : tone}>
      {children}
    </span>
  )
}

/** A bare rail container: steps placed inside join into one rail. `nested` uses the compact rail. */
export function PageRail({ nested, className, children }: { nested?: boolean; className?: string; children: React.ReactNode }) {
  return <div className={cx('rail', nested && 'gl-sub', className)}>{children}</div>
}
