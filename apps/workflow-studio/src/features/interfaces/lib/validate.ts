/**
 * Client-side form validation: a convenience only, the server (`shapes.validate`) is the authority (§8.7).
 * Messages are plain sentences shown inline under the field.
 */
import type { FormField } from './types'

export type FieldValues = Record<string, unknown>
export type FieldErrors = Record<string, string>

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const DATE = /^\d{4}-\d{2}-\d{2}$/

function isEmpty(v: unknown): boolean {
  return v === undefined || v === null || (typeof v === 'string' && v.trim() === '')
}

export function validateField(f: FormField, v: unknown): string | null {
  if (f.widget === 'checkbox') return f.required && v !== true ? 'This must be checked' : null
  if (isEmpty(v)) return f.required ? 'This field is required' : null
  if (f.widget === 'number' || f.widget === 'currency') {
    const n = typeof v === 'number' ? v : Number(v)
    if (!Number.isFinite(n)) return 'Enter a number'
    if (f.min !== undefined && n < f.min) return `Must be at least ${f.min}`
    if (f.max !== undefined && n > f.max) return `Must be at most ${f.max}`
    return null
  }
  const s = String(v)
  if (f.widget === 'select' || f.widget === 'radio') {
    if (f.options && !f.options.some((o) => String(o.value) === s)) return 'Choose one of the options'
    return null
  }
  if (f.widget === 'email' && !EMAIL.test(s.trim())) return 'Enter a valid email address'
  if (f.widget === 'date' && !DATE.test(s)) return 'Enter a valid date'
  if (f.minLength !== undefined && s.trim().length < f.minLength) return `Must be at least ${f.minLength} characters`
  if (f.maxLength !== undefined && s.trim().length > f.maxLength) return `Must be at most ${f.maxLength} characters`
  if (f.pattern) {
    try {
      if (!new RegExp(f.pattern).test(s.trim())) return 'Does not match the expected format'
    } catch {
      /* an invalid pattern is the server's problem */
    }
  }
  return null
}

export function validateFields(fields: FormField[], values: FieldValues): FieldErrors {
  const errors: FieldErrors = {}
  for (const f of fields) {
    const e = validateField(f, values[f.name])
    if (e) errors[f.name] = e
  }
  return errors
}

/** Typed body for submit: numbers become numbers, empty optional fields are dropped, strings are trimmed. */
export function toSubmitBody(fields: FormField[], values: FieldValues): FieldValues {
  const out: FieldValues = {}
  for (const f of fields) {
    const v = values[f.name]
    if (f.widget === 'checkbox') {
      out[f.name] = v === true
      continue
    }
    if (isEmpty(v)) continue
    if (f.widget === 'number' || f.widget === 'currency') out[f.name] = typeof v === 'number' ? v : Number(v)
    else if (f.widget === 'select' || f.widget === 'radio') {
      const opt = f.options?.find((o) => String(o.value) === String(v))
      out[f.name] = opt ? opt.value : v
    } else out[f.name] = String(v).trim()
  }
  return out
}

/**
 * Server field errors out of an ApiError body: `{detail: {fields: {name: "msg" | ["msg"]}}}`.
 * ASSUMED shape (§7 only says `fields?: {...}`); `{name: {message}}` is tolerated too.
 */
export function serverFieldErrors(details: Record<string, unknown>): FieldErrors {
  const raw = details.fields
  const out: FieldErrors = {}
  if (!raw || typeof raw !== 'object') return out
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'string') out[k] = v
    else if (Array.isArray(v) && typeof v[0] === 'string') out[k] = v[0]
    else if (v && typeof v === 'object' && typeof (v as { message?: unknown }).message === 'string') out[k] = (v as { message: string }).message
  }
  return out
}
