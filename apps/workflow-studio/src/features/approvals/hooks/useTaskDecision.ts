/**
 * Approve / Reject state for one task page view (DESIGN-STAGE5-UIS §5.4, §11.4).
 * - ONE Idempotency-Key per page view: a retried or double click is a replay, never a second decision.
 * - A ref lock closes the window before React re-renders `pending`, so a fast double click sends one request.
 * - The decided_by name is typed, not verified (R5); it is remembered in localStorage best effort.
 */
import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { tasksApi } from '@/shared/api/endpoints'
import { ApiError, errorMessage } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { loadDecidedBy, newIdempotencyKey, saveDecidedBy } from '@/features/interfaces/lib/keys'
import { serverFieldErrors } from '@/features/interfaces/lib/validate'
import type { DecideResult, TaskDetail } from '@/features/interfaces/lib/types'
import {
  decisionBody, describeOutcome, describeTaskState, initialFormData, stripFormDataPrefix, validateDecision,
  type OutcomeView,
} from '../lib/tasks'

export function useTaskDecision(task: TaskDetail, pid: string) {
  const qc = useQueryClient()
  const [idemKey] = useState(newIdempotencyKey)
  const [decidedBy, setDecidedByState] = useState(loadDecidedBy)
  const [comment, setComment] = useState('')
  const [formData, setFormData] = useState<Record<string, unknown>>(() => initialFormData(task.form))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<DecideResult | null>(null)
  const lock = useRef(false)

  const outcome: OutcomeView | null = result ? describeOutcome(result) : describeTaskState(task)
  const closed = task.status !== 'open' || result !== null

  const setDecidedBy = (v: string) => {
    setDecidedByState(v)
    if (errors.decided_by) setErrors((e) => ({ ...e, decided_by: '' }))
  }
  const setField = (name: string, v: unknown) => {
    setFormData((s) => ({ ...s, [name]: v }))
    if (errors[name]) setErrors((e) => ({ ...e, [name]: '' }))
  }

  const decide = async (decision: 'approved' | 'rejected') => {
    if (lock.current || closed) return
    const input = {
      decision, decidedBy, comment, formData, form: task.form,
      allowComment: task.allow_comment, requireCommentOnReject: task.require_comment_on_reject,
    }
    const errs = validateDecision(input)
    setErrors(errs)
    setFormError(null)
    if (Object.keys(errs).length > 0) return
    lock.current = true
    setPending(true)
    try {
      const res = await tasksApi.decide(task.id, decisionBody(input), idemKey)
      saveDecidedBy(decidedBy.trim())
      setResult(res)
      qc.setQueryData(qk.tasks.detail(task.id), res.task)
      void qc.invalidateQueries({ queryKey: qk.tasks.all(pid) })
      const view = describeOutcome(res)
      if (res.outcome === 'decided') toast.success(decision === 'approved' ? 'Approved' : 'Rejected')
      else toast.info(view.text)
    } catch (e) {
      if (e instanceof ApiError) {
        const fe = stripFormDataPrefix(serverFieldErrors(e.details))
        if (Object.keys(fe).length > 0) setErrors(fe)
        else setFormError(e.message)
      } else setFormError(errorMessage(e))
    } finally {
      lock.current = false
      setPending(false)
    }
  }

  return {
    decidedBy, setDecidedBy, comment, setComment, formData, setField, errors, formError, pending, closed, outcome, result,
    persistName: () => saveDecidedBy(decidedBy.trim()),
    approve: () => decide('approved'),
    reject: () => decide('rejected'),
  }
}

export type TaskDecisionState = ReturnType<typeof useTaskDecision>
