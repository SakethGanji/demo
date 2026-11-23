/**
 * The generated form (DESIGN-STAGE5-UIS §6.1, §11.2). Fields come from the engine's `/schema` response (or the
 * same mapping run on the FormTrigger shape in the editor preview). Client validation is a convenience: the
 * server decides, and its field errors render inline under the field. Double submit is blocked by a ref lock.
 */
import { Callout } from '@/shared/components/ui/surface'
import { useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Textarea } from '@/shared/components/ui/textarea'
import { ApiError, errorMessage } from '@/shared/api/http'
import { validateField, validateFields, serverFieldErrors, toSubmitBody, type FieldErrors, type FieldValues } from '../lib/validate'
import type { FormField } from '../lib/types'

export interface InterfaceFormProps {
  fields: FormField[]
  submitLabel?: string
  /** Throw to report failure; an ApiError with `fields` shows inline. */
  onSubmit: (body: FieldValues) => Promise<void> | void
  disabled?: boolean
  className?: string
}

function initialValues(fields: FormField[]): FieldValues {
  const v: FieldValues = {}
  for (const f of fields) if (f.widget === 'checkbox') v[f.name] = false
  return v
}

function FieldControl({
  f, value, error, disabled, onChange, onBlur,
}: {
  f: FormField
  value: unknown
  error?: string
  disabled?: boolean
  onChange: (v: unknown) => void
  onBlur: () => void
}) {
  const id = `iface-${f.name}`
  const tid = `iface-field-${f.name}`
  const invalid = error ? true : undefined
  const text = typeof value === 'string' || typeof value === 'number' ? String(value) : ''
  switch (f.widget) {
    case 'textarea':
      return <Textarea id={id} rows={4} value={text} placeholder={f.placeholder} disabled={disabled} aria-invalid={invalid} onBlur={onBlur} onChange={(e) => onChange(e.target.value)} data-testid={tid} />
    case 'number':
    case 'currency':
      return (
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          step={f.widget === 'currency' ? '0.01' : 'any'}
          min={f.min}
          max={f.max}
          value={text}
          placeholder={f.placeholder}
          disabled={disabled}
          aria-invalid={invalid}
          onBlur={onBlur}
          onChange={(e) => onChange(e.target.value)}
          data-testid={tid}
        />
      )
    case 'email':
      return <Input id={id} type="email" autoComplete="email" value={text} placeholder={f.placeholder} disabled={disabled} aria-invalid={invalid} onBlur={onBlur} onChange={(e) => onChange(e.target.value)} data-testid={tid} />
    case 'date':
      return <Input id={id} type="date" value={text} disabled={disabled} aria-invalid={invalid} onBlur={onBlur} onChange={(e) => onChange(e.target.value)} data-testid={tid} />
    case 'checkbox':
      return (
        <label className="flex items-center gap-2 text-[13px]">
          <Checkbox id={id} checked={value === true} disabled={disabled} onCheckedChange={(c) => onChange(c === true)} data-testid={tid} />
          <span>{f.placeholder ?? f.label}</span>
        </label>
      )
    case 'radio':
      return (
        <div role="radiogroup" aria-labelledby={`${id}-label`} className="flex flex-col gap-1.5" data-testid={tid}>
          {(f.options ?? []).map((o) => (
            <label key={String(o.value)} className="flex items-center gap-2 text-[13px]">
              <input
                type="radio"
                name={id}
                value={String(o.value)}
                checked={String(value ?? '') === String(o.value)}
                disabled={disabled}
                onChange={() => onChange(String(o.value))}
                onBlur={onBlur}
                className="accent-primary"
                data-testid={`${tid}-option-${String(o.value)}`}
              />
              <span>{o.label}</span>
            </label>
          ))}
        </div>
      )
    case 'select': {
      const items = Object.fromEntries((f.options ?? []).map((o) => [String(o.value), o.label]))
      return (
        <Select value={text || null} onValueChange={(v) => onChange(v ?? '')} items={items} disabled={disabled}>
          <SelectTrigger id={id} className="w-full" aria-invalid={invalid} data-testid={tid}>
            <SelectValue placeholder={f.placeholder ?? 'Choose…'} />
          </SelectTrigger>
          <SelectContent>
            {(f.options ?? []).map((o) => (
              <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    }
    case 'text':
    default:
      return <Input id={id} type="text" value={text} placeholder={f.placeholder} disabled={disabled} aria-invalid={invalid} onBlur={onBlur} onChange={(e) => onChange(e.target.value)} data-testid={tid} />
  }
}

export function InterfaceForm({ fields, submitLabel = 'Submit', onSubmit, disabled, className }: InterfaceFormProps) {
  const [values, setValues] = useState<FieldValues>(() => initialValues(fields))
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const lock = useRef(false)

  const setValue = (name: string, v: unknown) => {
    setValues((s) => ({ ...s, [name]: v }))
    if (errors[name]) setErrors((e) => { const n = { ...e }; delete n[name]; return n })
  }
  const blur = (f: FormField) => {
    const e = validateField(f, values[f.name])
    setErrors((s) => { const n = { ...s }; if (e) n[f.name] = e; else delete n[f.name]; return n })
  }

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (lock.current || disabled) return
    setFormError(null)
    const errs = validateFields(fields, values)
    setErrors(errs)
    if (Object.keys(errs).length > 0) return
    lock.current = true
    setSubmitting(true)
    try {
      await onSubmit(toSubmitBody(fields, values))
    } catch (e) {
      if (e instanceof ApiError) {
        const fe = serverFieldErrors(e.details)
        if (Object.keys(fe).length > 0) setErrors(fe)
        else setFormError(e.message)
      } else setFormError(errorMessage(e))
    } finally {
      lock.current = false
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} noValidate className={className ?? 'flex flex-col gap-4'} data-testid="iface-form">
      {fields.length === 0 && <div className="text-[12.5px] text-muted-foreground">This form has no fields.</div>}
      {fields.map((f) => (
        <Field
          key={f.name}
          label={f.widget === 'checkbox' ? undefined : (
            <span id={`iface-${f.name}-label`}>{f.label}{f.required ? <span className="text-destructive"> *</span> : null}</span>
          )}
          htmlFor={`iface-${f.name}`}
          help={f.help}
          error={errors[f.name]}
          errorTestId={`iface-field-error-${f.name}`}
        >
          <FieldControl f={f} value={values[f.name]} error={errors[f.name]} disabled={disabled || submitting} onChange={(v) => setValue(f.name, v)} onBlur={() => blur(f)} />
        </Field>
      ))}
      {formError && (
        <Callout tone="error" role="alert" data-testid="iface-form-error">
          {formError}
        </Callout>
      )}
      <div>
        <Button type="submit" disabled={disabled || submitting} data-testid="iface-submit">
          {submitting && <Loader2 className="animate-spin" />} {submitLabel}
        </Button>
      </div>
    </form>
  )
}
