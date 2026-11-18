import type { ReactNode } from 'react'

/** Plain-Unicode maths from the API (catalogue objectives, Vela's formulas) with `_x` / `_{…}` as subscripts and
 * `^x` / `^{…}` as superscripts, so "max_{j,s} T_b(x)" reads as typeset maths. Everything else is shown as given. */
export function MathText({ text, className }: { text: string; className?: string }) {
  const out: ReactNode[] = []
  const re = /([_^])(\{[^}]*\}|[A-Za-z0-9θλγηαρσεφμ∈ʲ⁽⁾]+)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    // keep snake_case identifiers (num_leaves, holdout_frac) intact: a subscript follows a single symbol, not a word
    const before = text.slice(0, m.index)
    const word = /[A-Za-z]{3,}$/.exec(before)
    if (word && !/(max|min|arg)$/.test(word[0])) continue
    out.push(before.slice(last))
    const body = m[2].startsWith('{') ? m[2].slice(1, -1) : m[2]
    out.push(m[1] === '_' ? <sub key={i++}>{body}</sub> : <sup key={i++}>{body}</sup>)
    last = m.index + m[0].length
  }
  out.push(text.slice(last))
  return <span className={className}>{out}</span>
}
