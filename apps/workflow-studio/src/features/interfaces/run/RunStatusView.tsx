/**
 * What the submitter sees about a run (DESIGN-STAGE5-UIS §6.1 RunView, §11.2). Titles only for waiting tasks,
 * the node *label* for progress, never node errors: `error.message` is already sanitised by the engine.
 */
import { CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react'
import { Progress } from '@/shared/components/ui/surface'
import { ScreenRenderer } from '../renderer/ScreenRenderer'
import { display } from '../lib/binding'
import { formatWhen } from '../lib/format'
import { humanize } from '../lib/formFields'
import { progressPercent, runHeadline } from '../lib/polling'
import type { RunView, ScreenSpec } from '../lib/types'

function ResultBody({ result, screen }: { result: Record<string, unknown>; screen?: ScreenSpec | null }) {
  if (screen) return <ScreenRenderer spec={screen} data={{ result, run: {} }} />
  const rows = Object.entries(result).slice(0, 50)
  return (
    <dl className="flex flex-col gap-2">
      {rows.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5">
          <dt className="text-[12px] font-medium text-muted-foreground">{humanize(k)}</dt>
          <dd className="whitespace-pre-wrap break-words text-[13px]">{display(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function RunStatusView({ view, resultScreen }: { view: RunView | undefined; resultScreen?: ScreenSpec | null }) {
  const status = view?.status ?? 'running'
  const pct = progressPercent(view)
  const Icon = status === 'success' ? CheckCircle2 : status === 'failed' || status === 'cancelled' ? XCircle : status === 'waiting' ? Clock : Loader2
  const tone = status === 'success' ? 'text-success-text' : status === 'failed' ? 'text-destructive' : 'text-muted-foreground'
  return (
    <div className="flex flex-col gap-4" data-testid="iface-run-status" data-status={status}>
      <div className="flex items-center gap-2.5">
        <Icon className={`size-5 shrink-0 ${tone} ${status === 'running' ? 'animate-spin' : ''}`} />
        <div className="text-[15px] font-semibold tracking-[-0.01em]" aria-live="polite">{runHeadline(view)}</div>
      </div>

      {view && (status === 'running' || status === 'waiting') && view.progress && (
        <div className="flex flex-col gap-1" data-testid="iface-run-progress">
          {pct !== null && <Progress value={pct} />}
          <div className="text-[12px] text-muted-foreground">
            {view.progress.done} of {view.progress.total} steps{view.progress.current ? ` · ${view.progress.current}` : ''}
          </div>
        </div>
      )}

      {view && view.waiting_for.length > 0 && status === 'waiting' && (
        <ul className="flex flex-col gap-1.5" data-testid="iface-run-waiting">
          {view.waiting_for.map((t) => (
            <li key={t.task_id} className="well flex items-center gap-2 px-3 py-2 text-[13px]" data-task-id={t.task_id}>
              <Clock className="size-3.5 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">Waiting for {t.title}</span>
              {t.due_at && <span className="text-[12px] text-muted-foreground">due {formatWhen(t.due_at)}</span>}
            </li>
          ))}
        </ul>
      )}

      {view && status === 'success' && (view.result ? (
        <div data-testid="iface-run-result"><ResultBody result={view.result} screen={resultScreen} /></div>
      ) : (
        <div className="text-[13px] text-muted-foreground" data-testid="iface-run-result">{view.message ?? 'Submitted'}</div>
      ))}

      {view && (status === 'failed' || status === 'cancelled') && (
        <div className="rounded-ctl bg-destructive-soft px-3 py-2 text-[12.5px] text-destructive" role="alert" data-testid="iface-run-error">
          {view.error?.message ?? (status === 'cancelled' ? 'This run was cancelled.' : 'Something went wrong.')}
        </div>
      )}
    </div>
  )
}
