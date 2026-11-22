import { useState } from 'react'
import type { MaskInfo, Unmask } from '@/shared/api/unmask'
import { useCan, why } from '@/shared/hooks/useCan'
import { useTeam } from '@/shared/lib/team'

/** U3 (F-PII, P20): the masking line of a computed page. Masked columns carry 🔒; "Unmask (reason)…" is greyed by
 *  /authz/check data.read_unmasked on the page's object (the run, or the dataset version) with the API's reason; a
 *  reason (2–500 characters, audited) re-reads the page with `unmasked=1&reason=`. An allowed read says so ("🔓 this
 *  read is audited") with Mask again; a refused one (audit mode) shows `unmasked_refused` and the page stays masked. */
export function MaskBar({ info, kind, id, value, onChange, what = 'values' }: {
  info: MaskInfo | null | undefined
  kind: 'run' | 'dataset'
  id: string | null | undefined
  value: Unmask
  onChange: (u: Unmask) => void
  /** what the masked values are on this page, for the sentence ("feature means", "bin edges", …) */
  what?: string
}) {
  const { actor } = useTeam()
  const can = useCan('data.read_unmasked', id ? kind : null, id ?? null, actor)
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const cols = info?.masked_columns ?? []
  const allowed = !!info?.unmasked && !info?.unmasked_refused
  if (!info || (!cols.length && !value)) return null
  const masks = info.masks ?? {}
  return (
    <div className="mask-bar" data-testid="mask-bar" data-kind={kind}>
      {value && allowed
        ? <span className="row" style={{ gap: 6 }} data-testid="unmasked-on">🔓 <b>unmasked</b> <span className="helper">“{value.reason}” · this read is audited</span>
          <button className="btn sm ghost" onClick={() => { onChange(null); setOpen(false); setReason('') }} data-testid="unmask-off">Mask again</button></span>
        : <>
          <span className="helper" data-testid="masked-note">🔒 {cols.length} masked {cols.length === 1 ? 'column' : 'columns'}: {cols.map((c) => <span key={c} className="mono" title={masks[c] ? `mask: ${masks[c]}` : undefined}>{c}{masks[c] ? ` (${masks[c]})` : ''} </span>)}· their {what} show as •••</span>
          {open
            ? <span className="row" style={{ gap: 6 }}>
              <input className="input" style={{ width: 230 }} autoFocus maxLength={500} placeholder="why you need the clear values (audited)" value={reason} onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && reason.trim().length >= 2 && onChange({ reason: reason.trim() })} data-testid="unmask-reason" />
              <button className="btn sm" disabled={reason.trim().length < 2} onClick={() => onChange({ reason: reason.trim() })} data-testid="unmask-go">Show</button>
              <button className="btn sm ghost" onClick={() => { setOpen(false); setReason(''); onChange(null) }}>✕</button></span>
            : <button className="btn sm" disabled={!actor || can?.allow === false} title={why(can, 'Show the clear values of the masked columns: give a reason; the read is audited')}
              onClick={() => setOpen(true)} data-testid="unmask">Unmask (reason)…</button>}
          {can && !can.allow && <span className="helper" data-testid="unmask-why">{why(can)}</span>}
        </>}
      {info.unmasked_refused && <div className="mask-refused" role="alert" data-testid="unmasked-refused">✕ <b>Unmasked read refused</b>: {info.unmasked_refused.message ?? info.unmasked_refused.reason ?? 'not allowed'}
        {info.unmasked_refused.needed ? ` (needs ${info.unmasked_refused.needed}${info.unmasked_refused.role ? `, you are ${info.unmasked_refused.role}` : ''})` : ''}. The page stays masked; the request was recorded.</div>}
    </div>
  )
}
