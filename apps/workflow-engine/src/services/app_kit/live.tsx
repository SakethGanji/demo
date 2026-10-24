/** Studio UI kit — live run views (provided by Studio, refreshed every builder turn; do not edit).
 * BrowserView polls a frame route (~1 s) and shows the latest screenshot inside browser chrome with URL,
 * live dot and elapsed clock; StepList and NetworkList render job events (see eventsToSteps / eventsToNetwork). */
import React, { useEffect, useRef, useState } from 'react'
import { cx, Spinner } from './ui'
import { fmtClock, fmtMs, toImageSrc, useElapsed, usePoll, type FrameLike, type NetworkRow, type Step } from './hooks'

/** HTML attributes passed to a component's root element (`id`, `style`, `data-*`, `aria-*`), minus names the kit uses itself. */
type Attrs<E = HTMLDivElement> = Omit<React.HTMLAttributes<E>, 'title' | 'children' | 'color' | 'content' | 'onChange' | 'onSelect' | 'defaultValue' | 'placeholder' | 'prefix'>

export interface BrowserViewProps extends Attrs<HTMLElement> {
  /** fetch the latest frame, e.g. `() => api.getRunFrame({ id: jobId })`; any FrameLike shape works (204/empty = no frame yet) */
  frame?: () => Promise<FrameLike>
  /** or pass an image src directly (data:/blob: URL) instead of `frame` */
  src?: string | null
  /** keep polling while true (default true); set false when the job is finished to freeze the last frame */
  live?: boolean
  intervalMs?: number
  /** restart polling (and clear the image) when this changes — pass the job id */
  pollKey?: string | null
  /** page URL shown in the address bar */
  url?: string | null
  /** @control text */
  title?: React.ReactNode
  /** @control text */
  subtitle?: React.ReactNode
  /** start time for the elapsed clock (ISO string, epoch ms or Date) */
  startedAt?: string | number | Date | null
  /** reserved: human take-over of the live browser. The button stays disabled until `takeOverEnabled` is true. */
  onTakeOver?: () => void
  takeOverEnabled?: boolean
  /** extra controls next to Take over (Stop, Cancel…) */
  controls?: React.ReactNode
  /** @control text */
  emptyText?: React.ReactNode
  className?: string
  headerClassName?: string
}

/** Live browser panel: polls `frame` and swaps the screenshot only when a new frame decodes (no flicker).
 * @category data
 * @example <BrowserView frame={() => api.get(`/runs/${id}/frame`)} pollKey={id} title="Live browser" /> */
export function BrowserView({ frame, src, live = true, intervalMs = 1000, pollKey, url, title = 'Live browser', subtitle, startedAt, onTakeOver,
  takeOverEnabled = false, controls, emptyText = 'Waiting for the first frame…', className, headerClassName, ...rest }: BrowserViewProps) {
  const poll = usePoll<FrameLike>(frame ?? (async () => null), intervalMs, { enabled: !!frame && live, deps: [pollKey ?? null] })
  const [img, setImg] = useState<string | null>(null)
  const [frames, setFrames] = useState(0)
  const lastBlob = useRef<string | null>(null)
  const lastSrc = useRef<string | null>(null)

  useEffect(() => { setImg(null); setFrames(0); lastSrc.current = null }, [pollKey])

  useEffect(() => {
    const next = src !== undefined ? src : toImageSrc(poll.data)
    if (!next || next === lastSrc.current) return
    lastSrc.current = next
    const pre = new Image()
    pre.onload = () => {
      setImg(next)
      setFrames((n) => n + 1)
      if (lastBlob.current && lastBlob.current !== next) URL.revokeObjectURL(lastBlob.current)
      lastBlob.current = next.startsWith('blob:') ? next : null
    }
    pre.src = next
  }, [poll.data, src])

  useEffect(() => () => { if (lastBlob.current) URL.revokeObjectURL(lastBlob.current) }, [])

  const elapsed = useElapsed(startedAt ?? null, live)
  return (
    <section data-vcomp="BrowserView" {...rest} className={cx('min-w-0 overflow-hidden rounded-xl border border-border bg-card', className)} data-testid="browser-view" data-frames={frames}>
      <header data-vpart="header" className={cx('flex items-center gap-2 border-b border-border px-3 py-2.5', headerClassName)}>
        <span className={cx('inline-block h-2 w-2 rounded-full', live ? 'animate-pulse bg-danger' : 'bg-input')} aria-label={live ? 'live' : 'stopped'} />
        <b className="truncate text-sm text-foreground">{title}</b>
        {subtitle && <span className="truncate text-xs text-muted-foreground">{subtitle}</span>}
        <span className="ml-auto font-mono text-xs text-muted-foreground" data-testid="browser-elapsed">{startedAt ? fmtClock(elapsed) : ''}</span>
      </header>
      <div className="m-3 overflow-hidden rounded-lg border border-input bg-card">
        <div className="flex items-center gap-2 border-b border-border bg-muted px-2.5 py-1.5">
          <span className="flex gap-1"><i className="h-2 w-2 rounded-full bg-input" /><i className="h-2 w-2 rounded-full bg-input" /><i className="h-2 w-2 rounded-full bg-input" /></span>
          <div className="min-w-0 flex-1 truncate rounded-md border border-border bg-card px-2 py-0.5 font-mono text-[11px] text-soft-foreground" data-testid="browser-url">
            <span className="text-success">● </span>{url || 'about:blank'}
          </div>
        </div>
        <div className="relative flex min-h-[180px] items-center justify-center bg-subtle">
          {img ? (
            <img src={img} alt="Live browser frame" className="block h-auto w-full" data-testid="browser-frame" />
          ) : (
            <div className="flex items-center gap-2 py-16 text-xs text-muted-foreground">{live && frame ? <Spinner size={12} /> : null}{emptyText}</div>
          )}
        </div>
      </div>
      <div data-vpart="footer" className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2.5">
        <button type="button" disabled={!takeOverEnabled || !onTakeOver} onClick={onTakeOver}
          title={takeOverEnabled ? 'Take control of this browser' : 'Take over is coming soon'}
          className="rounded-lg border border-input bg-card px-3 py-1.5 text-sm font-medium text-foreground disabled:cursor-not-allowed disabled:opacity-50" data-testid="take-over">
          Take over
        </button>
        {controls}
        <span className="ml-auto text-[11px] text-faint-foreground">{frames ? `${frames} frame${frames === 1 ? '' : 's'}` : ''}</span>
      </div>
    </section>
  )
}

const STEP_ICON: Record<string, React.ReactNode> = {
  passed: <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-success-soft text-[11px] font-bold text-success">✓</span>,
  failed: <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-danger-soft text-[11px] font-bold text-danger">✕</span>,
  blocked: <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-warning-soft text-[11px] font-bold text-warning">!</span>,
  skipped: <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-muted text-[11px] text-muted-foreground">–</span>,
  pending: <span className="inline-block h-[18px] w-[18px] rounded-full border-[1.5px] border-dashed border-input" />,
  running: <span className="grid h-[18px] w-[18px] place-items-center text-info"><Spinner size={13} /></span>,
}

/** Live step list: ✓ passed, ✕ failed (with detail), spinner + highlight for the running step, dashed circle for pending.
 * @category data
 * @example <StepList steps={steps} emptyText="No steps yet" /> */
export function StepList({ steps, emptyText = 'No steps yet', className, ...rest }: { steps: Step[]; /** @control text */ emptyText?: React.ReactNode; className?: string } & Attrs<HTMLElement>) {
  if (!steps.length) return <div data-vcomp="StepList" {...rest} className={cx('px-3 py-4 text-xs text-muted-foreground', className)}>{emptyText}</div>
  return (
    <ol data-vcomp="StepList" {...rest} className={cx('m-0 list-none py-1 pl-0', className)} data-testid="step-list">
      {steps.map((s) => (
        <li key={s.id} data-status={s.status}
          className={cx('grid grid-cols-[22px_1fr_auto] items-start gap-2 px-3 py-1.5 text-sm', s.status === 'running' && 'bg-info-soft', s.status === 'pending' && 'text-faint-foreground')}>
          {STEP_ICON[s.status] ?? STEP_ICON.pending}
          <span className="min-w-0">
            <span className="break-words">{s.title}</span>
            {s.detail && <span className={cx('mt-0.5 block text-xs', s.status === 'blocked' ? 'text-warning' : 'text-danger')}>{s.detail}</span>}
          </span>
          <span className="font-mono text-[11px] text-faint-foreground">{s.status === 'running' ? '…' : fmtMs(s.ms)}</span>
        </li>
      ))}
    </ol>
  )
}

/** Network calls of a run: method, URL, status (2xx green, 4xx/5xx red); policy blocks as amber rows. Shows the last `max`.
 * @category data
 * @example <NetworkList rows={network} max={50} /> */
export function NetworkList({ rows, max = 50, title = 'Network', className, ...rest }: { rows: NetworkRow[]; /** @min 1 @max 500 */ max?: number; /** @control text */ title?: React.ReactNode; className?: string } & Attrs) {
  const shown = rows.slice(-max)
  return (
    <div data-vcomp="NetworkList" {...rest} className={cx('px-3 py-2', className)} data-testid="network-list">
      {title && <h5 data-vpart="title" className="m-0 mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{title}{rows.length > max ? ` (last ${max} of ${rows.length})` : ''}</h5>}
      {shown.length === 0 && <div className="text-xs text-faint-foreground">No requests yet</div>}
      {shown.map((r) => (
        <div key={r.id} className={cx('grid grid-cols-[52px_1fr_auto] gap-2 py-0.5 font-mono text-[11px]', r.blocked ? 'rounded bg-warning-soft text-warning' : 'text-soft-foreground')}>
          <span className="font-semibold">{r.method}</span>
          <span className="truncate" title={r.reason ?? r.url}>{r.url}{r.blocked && r.reason ? ` — ${r.reason}` : ''}</span>
          <span className={cx(r.blocked ? 'font-semibold' : r.status == null ? 'text-faint-foreground' : r.status < 400 ? 'text-success' : 'text-danger')}>
            {r.blocked ? 'blocked' : r.status ?? '…'}{r.ms != null && !r.blocked ? <span className="ml-1 text-faint-foreground">{fmtMs(r.ms)}</span> : null}
          </span>
        </div>
      ))}
    </div>
  )
}
