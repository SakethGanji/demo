/** Pieces of the task page, shared by the default layout and the custom-screen slots (DESIGN-STAGE5-UIS §11.4). */
import { Loader2 } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Callout } from '@/shared/components/ui/surface'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Textarea } from '@/shared/components/ui/textarea'
import { display } from '@/features/interfaces/lib/binding'
import { humanize } from '@/features/interfaces/lib/formFields'
import type { TaskDetail } from '@/features/interfaces/lib/types'
import type { TaskDecisionState } from './hooks/useTaskDecision'
import { optionsOf, visibleContext } from './lib/tasks'

export function TaskContext({ context }: { context: Record<string, unknown> }) {
  const rows = visibleContext(context)
  return (
    <dl className="flex flex-col gap-2.5" data-testid="task-context">
      {rows.length === 0 && <div className="text-[12.5px] text-muted-foreground">No context was attached to this task.</div>}
      {rows.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5" data-context-key={k}>
          <dt className="text-[12px] font-medium text-muted-foreground">{k}</dt>
          <dd className="whitespace-pre-wrap break-words text-[13px]">{display(v) || <span className="text-faint">—</span>}</dd>
        </div>
      ))}
    </dl>
  )
}

/** The approver's extra inputs from the Approval node's `form` param. */
export function TaskFormFields({ task, d }: { task: TaskDetail; d: TaskDecisionState }) {
  if (task.form.length === 0) return <div data-testid="task-form" data-empty="" className="hidden" />
  const disabled = d.closed || d.pending
  return (
    <div className="flex flex-col gap-3" data-testid="task-form">
      {task.form.map((f) => {
        const id = `task-field-${f.name}`
        const v = d.formData[f.name]
        const text = typeof v === 'string' || typeof v === 'number' ? String(v) : ''
        const label = f.label ?? humanize(f.name)
        return (
          <Field key={f.name} label={f.type === 'checkbox' ? undefined : <>{label}{f.required ? <span className="text-destructive"> *</span> : null}</>} htmlFor={id} error={d.errors[f.name] || undefined} errorTestId={`task-field-error-${f.name}`}>
            {f.type === 'textarea' ? (
              <Textarea id={id} rows={3} value={text} disabled={disabled} onChange={(e) => d.setField(f.name, e.target.value)} data-testid={id} />
            ) : f.type === 'number' ? (
              <Input id={id} type="number" inputMode="decimal" step="any" value={text} disabled={disabled} onChange={(e) => d.setField(f.name, e.target.value)} data-testid={id} />
            ) : f.type === 'checkbox' ? (
              <label className="flex items-center gap-2 text-[13px]">
                <Checkbox id={id} checked={v === true} disabled={disabled} onCheckedChange={(c) => d.setField(f.name, c === true)} data-testid={id} />
                <span>{label}{f.required ? <span className="text-destructive"> *</span> : null}</span>
              </label>
            ) : f.type === 'select' ? (
              <Select value={text || null} onValueChange={(x) => d.setField(f.name, x ?? '')} items={Object.fromEntries(optionsOf(f).map((o) => [o.value, o.label]))} disabled={disabled}>
                <SelectTrigger id={id} className="w-full" data-testid={id}>
                  <SelectValue placeholder="Choose…" />
                </SelectTrigger>
                <SelectContent>
                  {optionsOf(f).map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            ) : (
              <Input id={id} type="text" value={text} disabled={disabled} onChange={(e) => d.setField(f.name, e.target.value)} data-testid={id} />
            )}
          </Field>
        )
      })}
    </div>
  )
}

/** Who is deciding (typed, not verified) and the optional comment. */
export function TaskIdentity({ task, d }: { task: TaskDetail; d: TaskDecisionState }) {
  const disabled = d.closed || d.pending
  return (
    <div className="flex flex-col gap-3">
      <Field label="Your name or email" help="Typed name, not verified. It is recorded with your decision." error={d.errors.decided_by || undefined} errorTestId="task-error-decided-by">
        <Input
          value={d.decidedBy}
          maxLength={200}
          autoComplete="name"
          disabled={disabled}
          onChange={(e) => d.setDecidedBy(e.target.value)}
          onBlur={d.persistName}
          data-testid="task-decided-by"
        />
      </Field>
      {task.allow_comment && (
        <Field label={task.require_comment_on_reject ? 'Comment (required to reject)' : 'Comment'} error={d.errors.comment || undefined} errorTestId="task-error-comment">
          <Textarea rows={2} value={d.comment} disabled={disabled} onChange={(e) => d.setComment(e.target.value)} data-testid="task-comment" />
        </Field>
      )}
    </div>
  )
}

export function TaskActions({ d, approveLabel = 'Approve', rejectLabel = 'Reject' }: { d: TaskDecisionState; approveLabel?: string; rejectLabel?: string }) {
  const disabled = d.closed || d.pending
  return (
    <div className="flex items-center gap-2">
      <Button variant="outline" onClick={d.reject} disabled={disabled} data-testid="task-reject">
        {rejectLabel}
      </Button>
      <Button onClick={d.approve} disabled={disabled} data-testid="task-approve">
        {d.pending && <Loader2 className="animate-spin" />} {approveLabel}
      </Button>
    </div>
  )
}

export function TaskOutcome({ d }: { d: TaskDecisionState }) {
  if (!d.outcome && !d.formError) return null
  return (
    <div className="flex flex-col gap-2">
      {d.formError && <Callout tone="error" role="alert" data-testid="task-error">{d.formError}</Callout>}
      {d.outcome && (
        <Callout tone={d.outcome.tone} role="status" data-testid="task-outcome" data-outcome={d.outcome.outcome}>
          {d.outcome.text}
        </Callout>
      )}
    </div>
  )
}
