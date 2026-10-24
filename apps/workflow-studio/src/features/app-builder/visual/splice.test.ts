import { describe, expect, it } from 'vitest'
import { parseSource, indexElements } from './source'
import type * as t from '@babel/types'
import {
  applyChecked, applyEdits, arrayInsert, arrayMove, arrayRemove, diffSize, printAttrValue, printLiteral, removeAttr, removeObjectKey,
  setAttr, setObjectKey, setText,
} from './splice'

function el(src: string, name: string, n = 0): t.JSXElement {
  return indexElements(parseSource(src)).filter((e) => e.name === name)[n].node
}

function arr(src: string): t.ArrayExpression {
  let hit: t.ArrayExpression | null = null
  const ast = parseSource(src)
  const visit = (n: unknown) => {
    if (!n || typeof n !== 'object' || hit) return
    const x = n as t.Node
    if (x.type === 'ArrayExpression') hit = x
    for (const k of Object.keys(x)) if (k !== 'loc') {
      const v = (x as unknown as Record<string, unknown>)[k]
      if (Array.isArray(v)) v.forEach(visit)
      else if (v && typeof v === 'object' && 'type' in v) visit(v)
    }
  }
  visit(ast.program)
  return hit!
}

const lines = (a: string, b: string) => diffSize(a, b)

describe('attributes', () => {
  const src = `import { Button } from './kit'
export function A() {
  return (
    <Button
      variant="primary"
      onClick={() => go()}
    >
      Save
    </Button>
  )
}
`
  it('replaces a string value in place', () => {
    const out = applyEdits(src, [setAttr(src, el(src, 'Button'), 'variant', '"danger"')])
    expect(out).toContain('variant="danger"')
    expect(lines(src, out)).toEqual({ added: 1, removed: 1 })
  })
  it('adds an attribute on its own line when the tag is one-per-line', () => {
    const out = applyEdits(src, [setAttr(src, el(src, 'Button'), 'size', '"sm"')])
    expect(out).toContain('      onClick={() => go()}\n      size="sm"\n    >')
    expect(lines(src, out)).toEqual({ added: 1, removed: 0 })
  })
  it('adds inline on a one-line tag', () => {
    const s = `const x = <Tag tone="red">A</Tag>\n`
    expect(applyEdits(s, [setAttr(s, el(s, 'Tag'), 'title', '"t"')])).toBe(`const x = <Tag tone="red" title="t">A</Tag>\n`)
    const bare = `const x = <Tag>A</Tag>\n`
    expect(applyEdits(bare, [setAttr(bare, el(bare, 'Tag'), 'tone', '"red"')])).toBe(`const x = <Tag tone="red">A</Tag>\n`)
  })
  it('removes a whole line, or just the attribute inline', () => {
    const out = applyEdits(src, [removeAttr(src, el(src, 'Button'), 'variant')!])
    expect(out).not.toContain('variant')
    expect(lines(src, out)).toEqual({ added: 0, removed: 1 })
    const s = `const x = <Tag tone="red" title="t">A</Tag>\n`
    expect(applyEdits(s, [removeAttr(s, el(s, 'Tag'), 'tone')!])).toBe(`const x = <Tag title="t">A</Tag>\n`)
  })
  it('edits text children keeping the whitespace', () => {
    const out = applyEdits(src, [setText(src, el(src, 'Button').children.find((c) => c.type === 'JSXText' && c.value.trim()) as t.JSXText, 'Submit', "'")])
    expect(out).toContain('\n      Submit\n')
    expect(lines(src, out)).toEqual({ added: 1, removed: 1 })
  })
  it('falls back to an expression for text JSX cannot hold', () => {
    const s = `const x = <b>Hi</b>\n`
    const n = el(s, 'b').children[0] as t.JSXText
    expect(applyEdits(s, [setText(s, n, 'a {b}', "'")])).toBe(`const x = <b>{'a {b}'}</b>\n`)
  })
})

describe('arrays', () => {
  const multi = `const COLUMNS = [
  { key: 'id', header: 'ID' },
  { key: 'amount', header: 'Amt', render: (r) => <b>{r.amount}</b> },
  { key: 'status', header: 'Status' },
]
`
  it('moves an item: only the two swapped lines change', () => {
    const out = applyEdits(multi, arrayMove(multi, arr(multi), 2, 1))
    expect(out.split('\n')[2]).toContain("'status'")
    expect(out.split('\n')[3]).toContain("'amount'")
    expect(lines(multi, out)).toEqual({ added: 1, removed: 1 })
  })
  it('removes an item line', () => {
    const out = applyEdits(multi, [arrayRemove(multi, arr(multi), 0)])
    expect(out).not.toContain("'id'")
    expect(lines(multi, out)).toEqual({ added: 0, removed: 1 })
  })
  it('inserts with the siblings indentation and trailing comma', () => {
    const out = applyEdits(multi, [arrayInsert(multi, arr(multi), 3, "{ key: 'notes', header: 'Notes' }")])
    expect(out).toContain("  { key: 'status', header: 'Status' },\n  { key: 'notes', header: 'Notes' },\n]")
    expect(lines(multi, out)).toEqual({ added: 1, removed: 0 })
    const mid = applyEdits(multi, [arrayInsert(multi, arr(multi), 1, "{ key: 'x', header: 'X' }")])
    expect(mid.split('\n')[2]).toBe("  { key: 'x', header: 'X' },")
  })
  it('handles single-line arrays', () => {
    const s = `const c = ['a', 'b', 'c']\n`
    expect(applyEdits(s, [arrayRemove(s, arr(s), 1)])).toBe(`const c = ['a', 'c']\n`)
    expect(applyEdits(s, [arrayRemove(s, arr(s), 2)])).toBe(`const c = ['a', 'b']\n`)
    expect(applyEdits(s, [arrayInsert(s, arr(s), 3, "'d'")])).toBe(`const c = ['a', 'b', 'c', 'd']\n`)
    expect(applyEdits(s, [arrayInsert(s, arr(s), 0, "'z'")])).toBe(`const c = ['z', 'a', 'b', 'c']\n`)
    expect(applyEdits(s, arrayMove(s, arr(s), 0, 2))).toBe(`const c = ['b', 'c', 'a']\n`)
    const e = `const c = []\n`
    expect(applyEdits(e, [arrayInsert(e, arr(e), 0, "'a'")])).toBe(`const c = ['a']\n`)
  })
  it('appends after a last item without a trailing comma', () => {
    const s = `const c = [\n  'a',\n  'b'\n]\n`
    expect(applyEdits(s, [arrayInsert(s, arr(s), 2, "'c'")])).toBe(`const c = [\n  'a',\n  'b',\n  'c'\n]\n`)
  })
})

describe('objects', () => {
  const s = `const c = [{ key: 'a', header: 'A' }]\n`
  const obj = () => arr(s).elements[0] as t.ObjectExpression
  it('sets, adds and removes keys inline', () => {
    expect(applyEdits(s, [setObjectKey(s, obj(), 'header', "'Amount'", "'")])).toBe(`const c = [{ key: 'a', header: 'Amount' }]\n`)
    expect(applyEdits(s, [setObjectKey(s, obj(), 'hidden', 'true', "'")])).toBe(`const c = [{ key: 'a', header: 'A', hidden: true }]\n`)
    expect(applyEdits(s, [removeObjectKey(s, obj(), 'header')!])).toBe(`const c = [{ key: 'a' }]\n`)
  })
  it('adds a key on its own line in a multi-line object', () => {
    const m = `const t = [\n  {\n    label: 'Open',\n    filter: { status: 'open' },\n  },\n]\n`
    const o = arr(m).elements[0] as t.ObjectExpression
    expect(applyEdits(m, [setObjectKey(m, o, 'tone', "'warn'", "'")])).toBe(`const t = [\n  {\n    label: 'Open',\n    filter: { status: 'open' },\n    tone: 'warn',\n  },\n]\n`)
  })
})

describe('printing and validation', () => {
  it('prints literals in the file style', () => {
    expect(printLiteral({ status: 'pending', n: 2, ok: true }, "'")).toBe("{ status: 'pending', n: 2, ok: true }")
    expect(printLiteral(['a', "it's"], "'")).toBe("['a', 'it\\'s']")
    expect(printLiteral('x', '"')).toBe('"x"')
    expect(printAttrValue('Plain', "'")).toBe('"Plain"')
    expect(printAttrValue('say "hi"', "'")).toBe(`{'say "hi"'}`)
    expect(printAttrValue(3, "'")).toBe('{3}')
  })
  it('refuses an edit that breaks the file', () => {
    expect(applyChecked('const a = 1\n', [{ start: 10, end: 11, text: '(' }])).toBeNull()
    expect(applyChecked('const a = 1\n', [{ start: 10, end: 11, text: '2' }])).toBe('const a = 2\n')
  })
  it('rejects overlapping edits', () => {
    expect(() => applyEdits('abcdef', [{ start: 0, end: 3, text: '' }, { start: 2, end: 4, text: '' }])).toThrow()
  })
})
