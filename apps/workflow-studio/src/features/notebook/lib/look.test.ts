import { describe, expect, it } from 'vitest'
import { avatarGradient, initials, isStatusColumn, moneyTotal, rowNoun, statusTone } from './look'

describe('statusTone', () => {
  it('maps known status words and leaves the rest neutral', () => {
    expect(statusTone('Settled')).toBe('ok')
    expect(statusTone('awaiting funds')).toBe('wait')
    expect(statusTone('pending')).toBe('wait')
    expect(statusTone('stuck')).toBe('bad')
    expect(statusTone('Repair')).toBe('bad')
    expect(statusTone('purple')).toBe('neutral')
    expect(statusTone(3)).toBe('neutral')
  })
})

describe('isStatusColumn', () => {
  const rows = [{ status: 'stuck', note: 'x' }, { status: 'sent', note: 'y' }, { status: 'stuck', note: 'z' }]
  it('detects by name or by low cardinality of status words', () => {
    expect(isStatusColumn('status', rows)).toBe(true)
    expect(isStatusColumn('note', rows)).toBe(false)
    expect(isStatusColumn('phase', [{ phase: 'pending' }, { phase: 'done' }, { phase: 'done' }])).toBe(true)
    expect(isStatusColumn('amount', [{ amount: 1 }])).toBe(false)
  })
})

describe('rowNoun', () => {
  it('reads the table noun from the SQL', () => {
    expect(rowNoun('select * from bank.wires where x')).toEqual({ one: 'wire', many: 'wires' })
    expect(rowNoun('SELECT a FROM payment_batches')).toEqual({ one: 'payment batch', many: 'payment batches' })
    expect(rowNoun('select 1 from status')).toEqual({ one: 'row', many: 'rows' })
    expect(rowNoun(undefined)).toEqual({ one: 'row', many: 'rows' })
  })
})

describe('moneyTotal', () => {
  it('totals an amount column only when all rows are present', () => {
    const rows = [{ amount: 100.5 }, { amount: 12500 }]
    expect(moneyTotal(['amount'], rows, 2)).toBe('total 12,600.50')
    expect(moneyTotal(['amount'], rows, 5)).toBeNull()
    expect(moneyTotal(['id'], [{ id: 1 }], 1)).toBeNull()
    expect(moneyTotal(['amount', 'currency'], [{ amount: 10, currency: 'USD' }], 1)).toBe('$10.00')
  })
})

describe('avatars', () => {
  it('is deterministic and gives Studio the brand gradient', () => {
    expect(avatarGradient('dana')).toBe(avatarGradient('Dana'))
    expect(avatarGradient('Studio')).toBeNull()
    expect(initials('Priya Raman')).toBe('PR')
    expect(initials('you')).toBe('Y')
  })
})
