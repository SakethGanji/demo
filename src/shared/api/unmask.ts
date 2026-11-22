// U3 (F-PII): the masking state a computed page answers with, and the `unmasked=1&reason=…&by=` query of an unmasked
// read. Every F-PII page (anomaly top rows, shift, active learning, annotations, Feature Lab, split options, intake)
// takes the same three query parameters; the read is decided as data.read_unmasked and audited.

/** What the API says about masking on a page (lake/mask.Mask.info). */
export type MaskInfo = {
  masked_columns?: string[]
  masks?: Record<string, string>
  /** true on an allowed unmasked read (a few pages answer {reason, columns}) */
  unmasked?: boolean | { reason?: string; columns?: string[] }
  /** audit mode: the read was asked for and refused; the page stays masked and a would-deny row was written */
  unmasked_refused?: { action?: string; needed?: string; role?: string | null; message?: string; reason?: string }
}

/** The person's choice: null = masked (the default), else the reason they gave. */
export type Unmask = { reason: string } | null

const actor = () => { try { return localStorage.getItem('ensemble.actor') || '' } catch { return '' } }

/** `unmasked=1&reason=…&by=…` (no leading separator), or '' when masked. */
export const unmaskParams = (u: Unmask | undefined): string =>
  u && u.reason.trim() ? new URLSearchParams({ unmasked: '1', reason: u.reason.trim(), ...(actor() ? { by: actor() } : {}) }).toString() : ''

/** Append the unmask query to a path that may already have one. */
export const withUnmask = (path: string, u: Unmask | undefined): string => {
  const q = unmaskParams(u)
  return q ? `${path}${path.includes('?') ? '&' : '?'}${q}` : path
}
