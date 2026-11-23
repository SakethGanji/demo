/**
 * Keep as…: turn the notebook into a workflow / test suite / scheduled check (and dataset / report where the engine
 * supports them). The strip lists every item POST keep/preview reports, with its mark and choices; Save is enabled
 * only when the engine says can_save.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { GlassSheet, ViewSwitch } from '@/shared/glass'
import { ApiError, errorMessage, isApiError } from '@/shared/api/http'
import { isNotBuilt, notebookApi } from '../api'
import { useNb } from '../context'
import type { KeepItem, KeepRequest, KeepResponse, KeepTarget, KeepTriggerType } from '../types'

export const TARGET_LABEL: Record<KeepTarget, string> = {
  workflow: 'Workflow', test_suite: 'Test suite', scheduled_check: 'Scheduled check', dataset: 'Dataset', report: 'Report',
}

const TRIGGERS: { value: KeepTriggerType; label: string; title?: string }[] = [
  { value: 'manual', label: 'Manual' },
  { value: 'cron', label: 'Schedule' },
  { value: 'business_day', label: 'Business day' },
  { value: 'mailbox', label: 'Mailbox · coming', title: 'There is no email trigger yet' },
  { value: 'after_deploy', label: 'After deploy' },
]

const MARK: Record<KeepItem['status'], string> = { ok: '✓', needs_choice: '?', blocked: '✗' }

export function KeepDrawer({ open, onOpenChange, initialTarget }: { open: boolean; onOpenChange: (o: boolean) => void; initialTarget: KeepTarget }) {
  const { nb, actions, me, slug, notify } = useNb()
  const [target, setTarget] = useState<KeepTarget>(initialTarget)
  const [trigger, setTrigger] = useState<KeepTriggerType>(initialTarget === 'scheduled_check' ? 'cron' : 'manual')
  const [cron, setCron] = useState('45 6 * * 1-5')
  const [name, setName] = useState(nb.title)
  const [choice, setChoice] = useState<Record<string, unknown>>({})
  const [saved, setSaved] = useState<KeepResponse | null>(null)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [flagged, setFlagged] = useState<string[]>([])
  useEffect(() => {
    if (open) {
      setTarget(initialTarget)
      setTrigger(initialTarget === 'scheduled_check' ? 'cron' : 'manual')
      setSaved(null)
      setSaveErr(null)
      setFlagged([])
    }
  }, [open, initialTarget])

  const body: KeepRequest = useMemo(() => {
    const params: KeepRequest['params'] = []
    const slots: KeepRequest['slots'] = []
    const writes: KeepRequest['writes'] = []
    let empty: KeepRequest['empty_policy'] = 'stop_quietly'
    for (const [k, v] of Object.entries(choice)) {
      const [kind, ...rest] = k.split(':')
      const id = rest.join(':')
      if (kind === 'params') params.push({ ref: id, mode: v === 'input' ? 'input' : 'fixed' })
      else if (kind === 'slots') slots.push({ cell_id: id, credential_id: (v as string) || null })
      else if (kind === 'writes') writes.push({ cell_id: id, mode: v === 'auto' ? 'auto' : 'approval' })
      else if (kind === 'empty_policy') empty = v === 'continue' ? 'continue' : 'stop_quietly'
    }
    return {
      target,
      trigger: { type: trigger, cron: trigger === 'cron' || trigger === 'business_day' ? cron : null },
      params, slots, writes, empty_policy: empty, name: name.trim() || null, by: me,
    }
  }, [choice, target, trigger, cron, name, me])

  const preview = useQuery({
    queryKey: ['notebook', nb.id, 'keep-preview', body, nb.updated_at],
    queryFn: () => notebookApi.keepPreview(nb.id, body),
    enabled: open && !saved,
    retry: false,
  })

  // "Saves as a draft until every item is ✓": while the engine says it can't be scheduled yet, the primary button
  // keeps it with the Manual trigger (a draft that runs when started) instead of asking for a refused schedule.
  const wouldSchedule = target !== 'test_suite' && trigger !== 'manual' && trigger !== 'mailbox'
  const asDraft = !!preview.data && !preview.data.can_schedule && (target === 'scheduled_check' || wouldSchedule)
  const save = () => {
    setSaveErr(null)
    setFlagged([])
    const req: KeepRequest = asDraft ? { ...body, trigger: { type: 'manual', cron: null } } : body
    actions.keep.mutate(req, {
      onSuccess: (r) => {
        setSaved(r)
        notify(`Kept as ${TARGET_LABEL[r.target].toLowerCase()}${asDraft ? ' (draft)' : ''}`, 'ok')
      },
      onError: (e) => {
        if (isApiError(e, 'unbound_credential')) {
          const its = (e instanceof ApiError ? (e.details.items as { key?: string }[] | undefined) : undefined) ?? []
          setFlagged(its.map((i) => i.key ?? '').filter(Boolean))
          setSaveErr('A credential slot is not bound: choose a secret for it above, or save it as a draft.')
        } else setSaveErr(errorMessage(e))
      },
    })
  }

  const items = preview.data?.items ?? []
  const value = (it: KeepItem) => {
    if (it.key in choice) return choice[it.key]
    if (it.kind === 'trigger') return trigger
    return it.choices?.[0]?.value
  }

  return (
    <GlassSheet open={open} onOpenChange={onOpenChange} side="right" title="Keep as…" data-testid="nb-keep">
      <div className="nb-keep">
        <ViewSwitch<KeepTarget>
          aria-label="Keep as"
          value={target}
          onChange={(t) => {
            setTarget(t)
            if (t === 'scheduled_check' && trigger === 'manual') setTrigger('cron')
          }}
          options={(['workflow', 'test_suite', 'scheduled_check', 'dataset', 'report'] as KeepTarget[]).map((t) => ({ value: t, label: TARGET_LABEL[t] }))}
        />
        <label className="nb-field">
          Name
          <input className="nb-input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Name" />
        </label>
        {target !== 'test_suite' && (
          <div className="nb-field">
            Trigger
            <ViewSwitch<KeepTriggerType> size="sm" aria-label="Trigger" value={trigger} onChange={setTrigger} options={TRIGGERS} />
            {(trigger === 'cron' || trigger === 'business_day') && (
              <input className="nb-input nb-mono" value={cron} onChange={(e) => setCron(e.target.value)} aria-label="Cron" placeholder="45 6 * * 1-5" />
            )}
          </div>
        )}

        {preview.isError ? (
          <div className="nb-card" data-testid="nb-keep-unavailable">
            {isNotBuilt(preview.error) ? 'Keep is not available on this engine yet.' : errorMessage(preview.error)}
          </div>
        ) : !preview.data && !saved ? (
          <div className="nb-faint">Checking what can be kept…</div>
        ) : null}

        {!saved && items.length > 0 && (
          <div className="flex flex-col gap-1.5" data-testid="nb-keep-strip">
            <div className="kind-label">
              {items.filter((i) => i.status === 'ok').length} ✓ · {items.filter((i) => i.status === 'needs_choice').length} ? · {items.filter((i) => i.status === 'blocked').length} ✗
            </div>
            {items.map((it) => (
              <div key={it.key} className="nb-keep-item" data-s={it.status} data-flagged={flagged.includes(it.key) || (flagged.length === 0 && !!saveErr && it.kind === 'slots' && it.status !== 'ok') ? 'true' : undefined} data-testid="nb-keep-item" data-kind={it.kind}>
                <span className="nb-keep-mark" aria-label={it.status}>{MARK[it.status]}</span>
                <div className="min-w-0">
                  <div>{it.message}</div>
                  {it.choices && it.choices.length > 0 && it.kind !== 'trigger' && (
                    <select
                      className="nb-select mt-1"
                      aria-label={`Choice for ${it.key}`}
                      value={String(value(it) ?? '')}
                      onChange={(e) => setChoice({ ...choice, [it.key]: e.target.value })}
                    >
                      {it.kind === 'slots' && <option value="">Not bound</option>}
                      {it.choices.map((c) => (
                        <option key={String(c.value)} value={String(c.value)}>{c.label}</option>
                      ))}
                    </select>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {saveErr && <div className="nb-err" data-testid="nb-keep-error">{saveErr}</div>}

        {saved ? (
          <div className="nb-card" data-tone="ok" data-testid="nb-keep-saved">
            <div>
              Kept as <b>{TARGET_LABEL[saved.target].toLowerCase()}</b>
              {saved.schedule_active ? ' · schedule active' : ''}
              {saved.table ? ` · table ${saved.table}` : ''}
            </div>
            {saved.webhook_url && <div className="nb-mono mt-1">POST {saved.webhook_url}</div>}
            <div className="nb-row mt-2">
              <a className="nb-linkbtn" href={`/p/${encodeURIComponent(slug)}/workflows/${encodeURIComponent(saved.workflow_id)}?view=page`}>Open the workflow ▸</a>
              {saved.target === 'test_suite' && <ExportSpec />}
            </div>
          </div>
        ) : (
          <div className="nb-row">
            {asDraft && <span className="nb-faint">Saves as a draft until every item is ✓</span>}
            <span className="nb-sp" />
            <button type="button" className="gl-btn" data-variant="ghost" onClick={() => onOpenChange(false)}>Cancel</button>
            <button
              type="button"
              className="gl-btn"
              data-variant="primary"
              disabled={!preview.data?.can_save || actions.keep.isPending}
              onClick={save}
              data-testid="nb-keep-save"
            >
              {asDraft ? 'Save as draft' : `Keep as ${TARGET_LABEL[target].toLowerCase()}`}
            </button>
          </div>
        )}
      </div>
    </GlassSheet>
  )
}

function ExportSpec() {
  const { nb } = useNb()
  const [err, setErr] = useState<string | null>(null)
  const go = async () => {
    setErr(null)
    try {
      const text = await notebookApi.exportSpec(nb.id)
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `${nb.title.replace(/[^\w-]+/g, '-').toLowerCase() || 'notebook'}.spec.ts`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setErr(isApiError(e, 'spec_export_unavailable') ? 'Export needs Node 23.6+ on the server' : isNotBuilt(e) ? 'Export is not available on this engine yet.' : errorMessage(e))
    }
  }
  return (
    <>
      <button type="button" className="gl-btn" data-size="sm" onClick={go}>Export .spec.ts</button>
      {err && <span className="nb-faint">{err}</span>}
    </>
  )
}
