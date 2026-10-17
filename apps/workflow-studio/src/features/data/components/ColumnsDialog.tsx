/**
 * Columns dialog (UI-DESIGN §3.4): edit the schema with `PUT …/tables/{tid} {columns}`.
 * `409 rows_incompatible {count}` → footer "12 rows violate this change" (`rows-incompatible`);
 * `422 invalid_column` inline.
 */
import { useEffect, useState } from 'react'
import { Columns3 } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage, isApiError } from '@/shared/api/http'
import type { DataTable } from '@/shared/api/types'
import { IconTile } from '@/shared/components/ui/surface'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { pluralize } from '@/features/project/lib/format'
import { ColumnsEditor } from './ColumnsEditor'
import { columnNameError, newColumnDraft, toColumns, type ColumnDraft } from '../lib/columns'
import { useUpdateTable } from '../useTableMutations'

export function ColumnsDialog({ table, open, onClose }: { table: DataTable; open: boolean; onClose: () => void }) {
  const { pid } = useProjectCtx()
  const update = useUpdateTable(pid)
  const [cols, setCols] = useState<ColumnDraft[]>([])
  const [incompatible, setIncompatible] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setCols(table.columns.length ? table.columns.map((c) => newColumnDraft(c)) : [newColumnDraft()])
      setIncompatible(null)
      setError(null)
    }
    // reset only when the dialog opens (a background refetch must not wipe edits)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, table.id])

  const hasErrors = cols.some((c) => columnNameError(c, cols))

  const save = () => {
    setIncompatible(null)
    setError(null)
    update.mutate(
      { id: table.id, body: { columns: toColumns(cols) } },
      {
        onSuccess: onClose,
        onError: (e) => {
          if (isApiError(e, 'rows_incompatible')) {
            const n = Number(e.details.count)
            setIncompatible(Number.isFinite(n) ? n : 0)
          } else setError(errorMessage(e))
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-[600px]" data-testid="columns-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconTile hue="data" className="size-6"><Columns3 /></IconTile> Columns · <span className="font-mono">{table.name}</span>
          </DialogTitle>
          <DialogDescription>
            Existing rows must fit the new columns. Renaming a column doesn't move its values; a required column needs a
            default when rows exist.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            save()
          }}
        >
          <ColumnsEditor
            value={cols}
            onChange={(v) => {
              setCols(v)
              setIncompatible(null)
              setError(null)
            }}
            testId="columns-editor"
          />
          {error && (
            <div className="text-[11.5px] text-destructive" role="alert" data-testid="columns-error">
              {error}
            </div>
          )}
          <DialogFooter className="items-center">
            {incompatible != null && (
              <span className="mr-auto text-[12px] text-destructive" role="alert" data-testid="rows-incompatible">
                {pluralize(incompatible, 'row')} {incompatible === 1 ? 'violates' : 'violate'} this change
              </span>
            )}
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={hasErrors || update.isPending} data-testid="columns-save">
              Save columns
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
