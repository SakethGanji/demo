import { describe, expect, it } from 'vitest'
import fixture from './form-field-mapping.fixture.json'
import { deriveFormFields, humanize, unsupportedFieldNames, widgetFor, withFieldOrder } from './formFields'
import type { Shape } from '@/features/workflow-editor/lib/shapes'

interface Case {
  name: string
  property: Shape
  required?: boolean
  fields_ui?: Record<string, unknown>
  expect: Record<string, unknown> & { widget: string | null }
}

describe('form-field mapping parity (shared fixture)', () => {
  for (const c of fixture.cases as unknown as Case[]) {
    it(c.name, () => {
      const schema: Shape = { type: 'object', properties: { f: c.property }, required: c.required ? ['f'] : [] }
      const fields = deriveFormFields(schema, { fieldsUi: c.fields_ui ? { f: c.fields_ui } : undefined })
      if (c.expect.widget === null) {
        expect(fields).toHaveLength(0)
        expect(unsupportedFieldNames(schema)).toEqual(['f'])
        return
      }
      expect(fields).toHaveLength(1)
      expect(fields[0]).toMatchObject({ name: 'f', ...c.expect })
    })
  }

  for (const c of fixture.order_cases as unknown as { name: string; properties: Record<string, Shape>; field_order?: string[]; expect_names: string[] }[]) {
    it(c.name, () => {
      const fields = deriveFormFields({ type: 'object', properties: c.properties }, { fieldOrder: c.field_order })
      expect(fields.map((f) => f.name)).toEqual(c.expect_names)
    })
  }
})

describe('formFields helpers', () => {
  it('humanizes names', () => {
    expect(humanize('order_id')).toBe('Order id')
    expect(humanize('reasonCode')).toBe('Reason code')
  })
  it('an unknown widget override is ignored', () => {
    expect(widgetFor({ type: 'string' }, 'nope' as never)).toBe('text')
  })
  it('the refund fixture schema derives the four expected widgets', () => {
    const schema: Shape = {
      type: 'object',
      required: ['order_id', 'amount', 'reason', 'email'],
      properties: {
        order_id: { type: 'string', pattern: '^A-\\d{5}$' },
        amount: { type: 'number', format: 'currency', minimum: 0.01, maximum: 100000 },
        reason: { type: 'string', enum: ['not_received', 'damaged', 'wrong_item', 'other'] },
        email: { type: 'string', format: 'email' },
      },
    }
    expect(deriveFormFields(schema).map((f) => [f.name, f.widget])).toEqual([
      ['order_id', 'text'],
      ['amount', 'currency'],
      ['reason', 'radio'],
      ['email', 'email'],
    ])
  })
})

describe('withFieldOrder', () => {
  const schema = (...names: string[]) => ({ type: 'object', properties: Object.fromEntries(names.map((n) => [n, { type: 'string' }])) })
  it('writes the order of the edited schema', () => {
    const out = withFieldOrder({ input_schema: schema('a') }, { input_schema: schema('b', 'a'), title: 'x' })
    expect(out.field_order).toEqual(['b', 'a'])
    expect(out.title).toBe('x')
  })
  it('reads a schema typed as JSON text', () => {
    expect(withFieldOrder({}, { input_schema: JSON.stringify(schema('z', 'y')) }).field_order).toEqual(['z', 'y'])
  })
  it('leaves parameters alone when the schema did not change', () => {
    const next = { input_schema: schema('a', 'b'), field_order: ['b', 'a'] }
    expect(withFieldOrder({ input_schema: schema('a', 'b') }, next)).toBe(next)
  })
})
