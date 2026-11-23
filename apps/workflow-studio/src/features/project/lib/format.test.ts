import { describe, expect, it } from 'vitest'
import { formatMs, parseTs, pluralize, shortAge, timeAgo } from './format'

const NOW = Date.parse('2026-10-15T12:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()
const MIN = 60_000
const HOUR = 60 * MIN

describe('formatMs', () => {
  it('rounds below 10 s and switches to seconds above', () => {
    expect(formatMs(331.4)).toBe('331 ms')
    expect(formatMs(9_999)).toBe('9999 ms')
    expect(formatMs(10_000)).toBe('10.0 s')
    expect(formatMs(12_345)).toBe('12.3 s')
  })
  it('shows a dash when unknown', () => {
    expect(formatMs(null)).toBe('—')
    expect(formatMs(undefined)).toBe('—')
    expect(formatMs(Number.NaN)).toBe('—')
  })
})

describe('parseTs', () => {
  it('parses ISO strings and rejects empty or invalid ones', () => {
    expect(parseTs('2026-10-15T12:00:00Z')?.getTime()).toBe(NOW)
    expect(parseTs('')).toBeNull()
    expect(parseTs(null)).toBeNull()
    expect(parseTs('nope')).toBeNull()
  })
})

describe('timeAgo', () => {
  it('says "now" for the first 45 seconds, including clock skew into the future', () => {
    expect(timeAgo(ago(0), NOW)).toBe('now')
    expect(timeAgo(ago(44_000), NOW)).toBe('now')
    expect(timeAgo(ago(-10_000), NOW)).toBe('now')
  })
  it('walks minutes, hours and days', () => {
    expect(timeAgo(ago(45_000), NOW)).toBe('1m ago')
    expect(timeAgo(ago(59 * MIN), NOW)).toBe('59m ago')
    expect(timeAgo(ago(60 * MIN), NOW)).toBe('1h ago')
    expect(timeAgo(ago(23 * HOUR), NOW)).toBe('23h ago')
    expect(timeAgo(ago(24 * HOUR), NOW)).toBe('yesterday')
    expect(timeAgo(ago(3 * 24 * HOUR), NOW)).toBe('3d ago')
  })
  it('switches to a calendar date after a week', () => {
    expect(timeAgo(ago(10 * 24 * HOUR), NOW)).toMatch(/Oct/)
  })
  it('is a dash without a usable timestamp', () => {
    expect(timeAgo(null, NOW)).toBe('—')
    expect(timeAgo('garbage', NOW)).toBe('—')
  })
})

describe('shortAge / pluralize', () => {
  it('drops the "ago"', () => {
    expect(shortAge(new Date(Date.now() - 5 * MIN).toISOString())).toBe('5m')
  })
  it('pluralises with an optional irregular form', () => {
    expect(pluralize(1, 'row')).toBe('1 row')
    expect(pluralize(0, 'row')).toBe('0 rows')
    expect(pluralize(2, 'index', 'indices')).toBe('2 indices')
  })
})
