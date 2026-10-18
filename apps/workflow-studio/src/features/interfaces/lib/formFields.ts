/**
 * Shape -> FormField mapping (DESIGN-STAGE5-UIS §4.1). Mirrors the engine's `engine/form_fields.py`; the
 * parity test (`formFields.test.ts`) runs both sides' agreed table from `form-field-mapping.fixture.json`.
 *
 * Widget derivation (frozen):
 *   string -> text; maxLength > 200 -> textarea; format email -> email; format date -> date
 *   enum -> select (<= 7 options -> radio)
 *   number/integer -> number; format currency -> currency
 *   boolean -> checkbox
 *   array / object -> unsupported (WF013; the form omits them)
 * ASSUMED precedence where the table is silent: `fields_ui.widget` > unsupported type > enum > type/format.
 */
import type { Shape } from '@/features/workflow-editor/lib/shapes'
import type { FieldsUi, FormField, FormWidget } from './types'

export const RADIO_MAX_OPTIONS = 7
export const TEXTAREA_MIN_LENGTH = 200
/** A string field with no maxLength gets this one (engine/form_fields.py DEFAULT_MAX_LENGTH). */
export const DEFAULT_MAX_LENGTH = 4000

const WIDGETS: readonly FormWidget[] = ['text', 'textarea', 'number', 'currency', 'select', 'radio', 'checkbox', 'email', 'date']

export function isWidget(v: unknown): v is FormWidget {
  return typeof v === 'string' && (WIDGETS as readonly string[]).includes(v)
}

const SCALAR_TYPES = ['string', 'number', 'integer', 'boolean']

/** Same rule as the engine's `is_renderable`: only scalar types become form fields. */
export function isUnsupported(shape: Shape): boolean {
  return !SCALAR_TYPES.includes(shape.type as string)
}

/** The widget for one property, or null when it cannot be rendered. */
export function widgetFor(shape: Shape, override?: FormWidget): FormWidget | null {
  if (isUnsupported(shape)) return null
  if (override && isWidget(override)) return override
  if (Array.isArray(shape.enum) && shape.enum.length > 0) return shape.enum.length <= RADIO_MAX_OPTIONS ? 'radio' : 'select'
  switch (shape.type) {
    case 'boolean':
      return 'checkbox'
    case 'number':
    case 'integer':
      return shape.format === 'currency' ? 'currency' : 'number'
    case 'string':
    default:
      if (shape.format === 'email') return 'email'
      if (shape.format === 'date') return 'date'
      if (typeof shape.maxLength === 'number' && shape.maxLength > TEXTAREA_MIN_LENGTH) return 'textarea'
      return 'text'
  }
}

/** "order_id" -> "Order id" */
export function humanize(name: string): string {
  const s = name.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim().toLowerCase()
  return s ? s[0].toUpperCase() + s.slice(1) : name
}

export interface DeriveOptions {
  fieldOrder?: string[]
  fieldsUi?: Record<string, FieldsUi>
}

/** Property names in render order: `field_order` first (known names only), then schema order. */
export function orderedNames(schema: Shape, order?: string[]): string[] {
  const props = Object.keys(schema.properties ?? {})
  if (!order || order.length === 0) return props
  const head = order.filter((n) => props.includes(n))
  return [...head, ...props.filter((n) => !head.includes(n))]
}

export function deriveFormFields(schema: Shape | null | undefined, opts: DeriveOptions = {}): FormField[] {
  if (!schema || !schema.properties) return []
  const required = new Set(schema.required ?? [])
  const out: FormField[] = []
  for (const name of orderedNames(schema, opts.fieldOrder)) {
    const shape = schema.properties[name]
    const ui = opts.fieldsUi?.[name]
    const widget = widgetFor(shape, ui?.widget)
    if (!widget) continue
    const f: FormField = { name, label: ui?.label || humanize(name), widget, required: required.has(name) }
    if (ui?.help) f.help = ui.help
    if (ui?.placeholder) f.placeholder = ui.placeholder
    if (Array.isArray(shape.enum) && shape.enum.length > 0) f.options = shape.enum.map((v) => ({ value: v as string | number, label: typeof v === 'string' ? humanize(v) : String(v) }))
    if (typeof shape.minimum === 'number') f.min = shape.minimum
    if (typeof shape.maximum === 'number') f.max = shape.maximum
    if (typeof shape.minLength === 'number' && shape.type === 'string') f.minLength = shape.minLength
    if (shape.type === 'string') f.maxLength = typeof shape.maxLength === 'number' ? shape.maxLength : DEFAULT_MAX_LENGTH
    else if (typeof shape.maxLength === 'number') f.maxLength = shape.maxLength
    if (shape.pattern) f.pattern = shape.pattern
    out.push(f)
  }
  return out
}

/** Names the form omits (arrays / nested objects): the checker warns WF013 for these. */
export function unsupportedFieldNames(schema: Shape | null | undefined): string[] {
  if (!schema?.properties) return []
  return Object.entries(schema.properties).filter(([, s]) => isUnsupported(s)).map(([n]) => n)
}

function schemaOf(raw: unknown): Shape | null {
  if (typeof raw === 'string') {
    try {
      return schemaOf(JSON.parse(raw))
    } catch {
      return null
    }
  }
  return raw && typeof raw === 'object' ? (raw as Shape) : null
}

/**
 * JSONB does not keep key order, so the author's field order has to be stored on its own: the FormTrigger's
 * `field_order` param. When `input_schema` changed, `field_order` becomes the property order the author sees now
 * (new names appended, removed names dropped). Parameters that did not touch the schema are returned as they are.
 */
export function withFieldOrder(prev: Record<string, unknown> | undefined, next: Record<string, unknown>): Record<string, unknown> {
  if (JSON.stringify(prev?.input_schema ?? null) === JSON.stringify(next.input_schema ?? null)) return next
  const names = Object.keys(schemaOf(next.input_schema)?.properties ?? {})
  const current = Array.isArray(next.field_order) ? (next.field_order as unknown[]).filter((n): n is string => typeof n === 'string') : []
  if (current.length === names.length && current.every((n, i) => n === names[i])) return next
  return { ...next, field_order: names }
}
