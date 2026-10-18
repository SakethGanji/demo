import { createElement } from 'react'
import { kindIcon, type KgKind } from './kinds'

/** The icon of a kind, as an element (so callers never create a component during render). */
export function KindIcon({ kind, subkind, className }: { kind: KgKind; subkind?: string | null; className?: string }) {
  return createElement(kindIcon(kind, subkind), { className, 'aria-hidden': true })
}
