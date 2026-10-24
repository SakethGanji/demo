/** Retool-style wiring for the selected element: Data, On click and Visible when, each a minimal splice of the source. */
import { useMemo, useState } from 'react'
import type { Op } from '../editor'
import type { VisualEdit } from '../useVisualEdit'
import { AskFix } from '../AskFix'
import { Note } from './controls'
import { commitBuilt } from './commit'
import { DataSection } from './DataSection'
import { ClickSection } from './ClickSection'
import { VisibleSection } from './VisibleSection'
import { apiOps, locateCtx, type Built } from './scope'

export function BindPanel({ ve }: { ve: VisualEdit }) {
  const { selection, view, files } = ve
  // a click on a kit list item (a DataTable cell is `columns.<i>`) selects a part; the wiring belongs to the element itself
  const ctx = useMemo(() => (selection ? locateCtx(files, { file: selection.file, key: selection.key }) : null), [files, selection])
  const ops = useMemo(() => apiOps(files), [files])
  const [error, setError] = useState<string | null>(null)
  const [errorFor, setErrorFor] = useState(selection)
  if (errorFor !== selection) {
    setErrorFor(selection)
    setError(null)
  }
  if (!ctx || !view || !selection) return null
  const disabled = ve.locked
  const run = (b: Built) => setError(commitBuilt(ve, b))
  const edit = (o: Op[]) => setError(ve.editAt({ file: selection.file, key: selection.key, part: null }, o))
  // a fresh draft per element (and per source change, so a picker never shows a stale choice)
  const k = `${selection.file}:${selection.key}:${ctx.src.length}`
  return (
    <div className="flex flex-col gap-3" data-testid="vbind-panel">
      {error && <Note tone="error" testId="vbind-error">{error}</Note>}
      {error && <AskFix view={view} />}
      <DataSection key={`d:${k}`} ctx={ctx} view={view} ops={ops} disabled={disabled} run={run} edit={edit} />
      <ClickSection key={`c:${k}`} ctx={ctx} view={view} ops={ops} disabled={disabled} run={run} />
      <VisibleSection key={`v:${k}`} ctx={ctx} view={view} disabled={disabled} run={run} />
    </div>
  )
}
