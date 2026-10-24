import { ValueToken } from '@/shared/glass'
import { useNb } from '../context'
import { getPath, isRef, valueText } from '../lib/cells'
import type { Cell } from '../types'

/** A value token bound to a cell spec path. Editing it PATCHes the spec (the cell and the ones after go stale). */
export function Tok({ cell, path, label, placeholder, number }: { cell: Cell; path: string; label: string; placeholder?: string; number?: boolean }) {
  const { editSpec, live, nb } = useNb()
  const v = getPath(cell.spec, path)
  const editable = !live && !cell.applied
  return (
    <ValueToken
      value={valueText(v, nb.cells)}
      label={label}
      title={isRef(v) ? v.trim() : undefined}
      path={`${cell.name}.${path}`}
      placeholder={placeholder}
      inputType={number || typeof v === 'number' ? 'number' : 'text'}
      changed={cell.stale && !!cell.latest}
      hint={cell.applied ? undefined : 'The cell and the ones after it go stale · then Rerun from here'}
      onCommit={editable ? (next) => editSpec(cell, path, next) : undefined}
    />
  )
}

/** A read-only token (refs, computed values). */
export function RoTok({ value, label }: { value: unknown; label?: string }) {
  const { nb } = useNb()
  return <ValueToken value={valueText(value, nb.cells)} label={label} title={isRef(value) ? value.trim() : undefined} />
}
