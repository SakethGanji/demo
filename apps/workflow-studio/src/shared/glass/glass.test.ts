import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { createElement as h } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  CheckStrip, Chip, DataTablePreview, DiffBlock, PageBranch, PageForEach, PageLoop, PageSection, PageStep, StatusMark, ValueToken,
  cx, filterPalette, formatCell, summarizeChecks, STEP_STATUSES,
} from './index'

const html = (el: React.ReactElement) => renderToStaticMarkup(el)
const dir = path.dirname(new URL(import.meta.url).pathname)

describe('glass.css', () => {
  const css = fs.readFileSync(path.join(dir, 'glass.css'), 'utf8')
  it('defines every public utility from BUILD-PLAN §1.4', () => {
    for (const u of ['glass-page', 'rail', 'kind-label', 'vtoken', 'diff-line-add', 'diff-line-del', 'chip-ok', 'chip-warn', 'chip-prod', 'chip-ai', 'chip-brand', 'check-strip', 'sheet-3pane', 'drawer']) {
      expect(css, u).toMatch(new RegExp(`@utility ${u} \\{`))
    }
  })
  it('defines the missing tokens in light and dark', () => {
    for (const t of ['--sky', '--mono-label', '--vtoken-underline', '--rail-path', '--brand-grad']) expect(css).toContain(`${t}:`)
    const dark = css.slice(css.indexOf('.dark {'))
    expect(dark).toContain('--sky:')
  })
  it('uses no solid borders on surfaces', () => {
    expect(css).not.toMatch(/border:\s*1px solid/)
  })
  it('is imported exactly once from index.css', () => {
    const index = fs.readFileSync(path.join(dir, '../../index.css'), 'utf8')
    expect(index.split('@import "./shared/glass/glass.css";').length - 1).toBe(1)
  })
})

describe('cx', () => {
  it('keeps custom utilities that tailwind-merge could drop', () => {
    expect(cx('kind-label', 'text-sm', 'chip-ok', false, 'vtoken')).toBe('kind-label text-sm chip-ok vtoken')
  })
})

describe('PageStep', () => {
  it('renders mark, kind label, name, sentence, result, annotation and actions', () => {
    const out = html(
      h(PageStep, {
        status: 'ok', kind: 'query', label: 'Find wires', kindParts: ['bank-prod'], meta: '120 ms', onPath: true,
        sentence: h('span', null, 'Find ', h(ValueToken, { value: 10, label: 'minutes' })),
        result: '4 rows', annotation: 'Priya · via bank-prod', actions: h('button', null, 'Rerun'),
      }),
    )
    expect(out).toContain('class="gl-step"')
    expect(out).toContain('data-status="ok"')
    expect(out).toContain('data-path=""')
    expect(out).toContain('aria-label="Done"')
    expect(out).toMatch(/class="kind-label"[^>]*data-kind="query"/)
    expect(out).toContain('bank-prod')
    expect(out).toContain('Find wires')
    expect(out).toContain('class="vtoken"')
    expect(out).toContain('4 rows')
    expect(out).toContain('Priya · via bank-prod')
    expect(out).toMatch(/<button type="button" class="gl-step-ann" aria-expanded="false"/)
    expect(out).toContain('data-hover=""')
    expect(out).toContain('Rerun')
  })
  it('marks stale with a note and dim', () => {
    const out = html(h(PageStep, { status: 'stale', kind: 'note', sentence: 'x', stale: 'from run 1', dim: true }))
    expect(out).toContain('data-stale=""')
    expect(out).toContain('data-dim=""')
    expect(out).toContain('from run 1')
  })
  it('PageSection puts steps on one rail with a § heading', () => {
    const out = html(h(PageSection, { id: 's1', num: 1, title: 'Find' }, h(PageStep, { status: 'ok', kind: 'a', sentence: 'a' })))
    expect(out).toContain('§ 1')
    expect(out).toContain('class="rail"')
    expect(out).toContain('aria-labelledby="s1-title"')
  })
})

describe('StatusMark', () => {
  it('covers every status with an accessible name', () => {
    for (const s of STEP_STATUSES) expect(html(h(StatusMark, { status: s }))).toMatch(new RegExp(`data-status="${s}"`))
  })
})

describe('ValueToken', () => {
  it('is a read-only span without onCommit, a trigger button with it', () => {
    expect(html(h(ValueToken, { value: 'bank.wires' }))).toMatch(/^<span[^>]*data-readonly=""[^>]*class="vtoken"/)
    const editable = html(h(ValueToken, { value: 5, label: 'n', onCommit: () => {} }))
    expect(editable).toMatch(/^<button/)
    expect(editable).toContain('aria-label="n: 5"')
  })
  it('shows the placeholder when empty', () => {
    const out = html(h(ValueToken, { value: '', placeholder: 'pick a table' }))
    expect(out).toContain('data-empty=""')
    expect(out).toContain('pick a table')
  })
})

describe('PageBranch', () => {
  const cases = [
    { id: 'a', label: 'First', cond: 'x', children: h(PageStep, { status: 'skip', kind: 'k', sentence: 'body-a' }) },
    { id: 'b', label: 'Taken', cond: 'y', taken: true, children: h(PageStep, { status: 'ok', kind: 'k', sentence: 'body-b' }) },
    { id: 'c', label: 'Last', children: h(PageStep, { status: 'skip', kind: 'k', sentence: 'body-c' }) },
  ]
  it('opens the taken case, folds the others and greens the spine above it', () => {
    const out = html(h(PageBranch, { status: 'ok', sentence: 'If', cases }))
    expect(out).toContain('body-b')
    expect(out).not.toContain('body-a')
    expect(out).not.toContain('body-c')
    expect(out).toMatch(/data-case="a"[^>]*data-pass=""/)
    expect(out).toMatch(/data-case="b" data-taken=""/)
    expect(out).toContain('data-kind="if"')
  })
  it('defaultOpen=all opens every case', () => {
    const out = html(h(PageBranch, { status: 'ok', sentence: 'If', cases, defaultOpen: 'all' }))
    for (const b of ['body-a', 'body-b', 'body-c']) expect(out).toContain(b)
  })
})

describe('PageLoop / PageForEach', () => {
  it('loop shows one chip per iteration with the selected one pressed and failures red', () => {
    const out = html(h(PageLoop, { status: 'ok', sentence: 'each', iterations: [{ status: 'ok' }, { status: 'fail' }], value: 1, onChange: () => {} }))
    expect(out).toContain('2 iterations')
    expect(out).toMatch(/aria-pressed="true"[^>]*data-r="fail"/)
  })
  it('loop collapses to a pager over 12 iterations', () => {
    const out = html(h(PageLoop, { status: 'ok', sentence: 'each', iterations: 40, value: 0, onChange: () => {} }))
    expect(out).toContain('1 / 40')
    expect(out).toContain('aria-label="Previous iteration" disabled=""')
  })
  it('for-each summarises and lists only failed rows by default', () => {
    const out = html(
      h(PageForEach, {
        status: 'fail', sentence: 'each', total: 150, ok: 148, failed: 2,
        rows: [{ index: 1, label: 'ok-row', status: 'ok' }, { index: 2, label: 'bad-row', status: 'fail', why: '409' }],
      }),
    )
    expect(out).toContain('148 ✓')
    expect(out).toContain('2 ✗')
    expect(out).toContain('bad-row')
    expect(out).not.toContain('ok-row')
  })
})

describe('content', () => {
  it('DiffBlock marks lines with the diff utilities and counts', () => {
    const out = html(h(DiffBlock, { lines: [{ kind: 'del', text: 'a' }, { kind: 'add', text: 'b' }, { kind: 'add', text: 'c' }] }))
    expect(out).toContain('gl-diff-line diff-line-del')
    expect(out).toContain('gl-diff-line diff-line-add')
    expect(out).toContain('+2 −1')
  })
  it('CheckStrip draws the last N and summarises', () => {
    const out = html(h(CheckStrip, { results: ['fail', 'pass', 'pass', 'fail', 'pass'], max: 4, summary: true }))
    expect((out.match(/class="gl-check"/g) ?? []).length).toBe(4)
    expect(out).toContain('3/4')
    expect(summarizeChecks(['pass', 'skip', { result: 'fail' }])).toEqual({ pass: 1, fail: 1, ran: 2 })
  })
  it('DataTablePreview shows 3 rows and the total', () => {
    const rows = Array.from({ length: 5 }, (_, i) => ({ id: i, name: `n${i}` }))
    const out = html(h(DataTablePreview, { rows, total: 1200, onMore: () => {} }))
    expect((out.match(/<tr>/g) ?? []).length).toBe(4) // header + 3
    expect(out).toContain('Show all 1,200 rows ▾')
    expect(formatCell(null)).toEqual({ text: 'null', num: false, nil: true })
    expect(formatCell(1234.5).text).toBe('1,234.5')
  })
  it('Chip maps tones to the chip utilities', () => {
    expect(html(h(Chip, { tone: 'prod' }, 'x'))).toContain('class="gl-chip chip-prod"')
    expect(html(h(Chip, { tone: 'neutral' }, 'x'))).toContain('class="gl-chip"')
  })
  it('filterPalette matches every word across label, group and keywords', () => {
    const items = [
      { id: 'a', label: 'Query a database', group: 'Add', keywords: 'sql', onSelect: () => {} },
      { id: 'b', label: 'Call an API', group: 'Add', onSelect: () => {} },
    ]
    expect(filterPalette(items, 'sql add').map((i) => i.id)).toEqual(['a'])
    expect(filterPalette(items, '  ').length).toBe(2)
  })
})
