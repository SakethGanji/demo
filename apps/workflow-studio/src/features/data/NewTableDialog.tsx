/**
 * New table dialog (UI-DESIGN §3.4): name + column rows. `409 name_taken` / `422 invalid_name`
 * show on the name field and `422 invalid_column` under the columns, all inline (no toast).
 * Opened from `?dialog=new-table` by WorkspaceOverlays.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Table2 } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage, isApiError } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { ColumnsEditor } from './components/ColumnsEditor'
import { columnNameError, newColumnDraft, toColumns, type ColumnDraft } from './lib/columns'
import { useCreateTable } from './useTableMutations'

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/

export function NewTableDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const create = useCreateTable(pid)
  const [name, setName] = useState('')
  const [cols, setCols] = useState<ColumnDraft[]>(() => [newColumnDraft()])
  const [nameError, setNameError] = useState<string | null>(null)
  const [colError, setColError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setCols([newColumnDraft()])
      setNameError(null)
      setColError(null)
      create.reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const localNameError = name && !NAME_RE.test(name) ? 'Use letters, digits and _ (not starting with a digit).' : null
  const hasColErrors = cols.some((c) => columnNameError(c, cols))

  const submit = () => {
    setNameError(null)
    setColError(null)
    if (!name || localNameError || hasColErrors) return
    create.mutate(
      { name, columns: toColumns(cols) },
      {
        onSuccess: (t) => {
          onClose()
          navigate({ to: '/p/$project/data/$table', params: { project: slug, table: t.name } })
        },
        onError: (e) => {
          if (isApiError(e, 'name_taken')) setNameError(`A table called "${name}" already exists.`)
          else if (isApiError(e, 'invalid_name')) setNameError(errorMessage(e))
          else if (isApiError(e, 'invalid_column')) setColError(errorMessage(e))
          else setColError(errorMessage(e))
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-[560px]" data-testid="new-table-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Table2 className="size-4 text-primary" /> New table
          </DialogTitle>
          <DialogDescription>
            One table for the whole project: dev and prod, tests and the published app all read and write these rows.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              submit()
            }
          }}
        >
          <Field label="Table name" error={nameError ?? localNameError} errorTestId="new-table-name-error">
            <Input
              autoFocus
              value={name}
              onChange={(e) => {
                setName(e.target.value.trim())
                setNameError(null)
              }}
              placeholder="refunds"
              className="font-mono"
              aria-invalid={!!(nameError ?? localNameError)}
              data-testid="new-table-name"
            />
          </Field>
          <Field
            label="Columns"
            error={colError}
            errorTestId="new-table-columns-error"
            help={
              <>
                Every row also gets <span className="font-mono">id</span> and <span className="font-mono">created_at</span>.
                Code reads it with <span className="font-mono">studio.db.table("{name || 'name'}")</span>.
              </>
            }
          >
            <ColumnsEditor value={cols} onChange={(v) => {
                setCols(v)
                setColError(null)
              }} testId="new-table-columns" />
          </Field>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!name || !!localNameError || hasColErrors || create.isPending}
              data-testid="new-table-create"
            >
              Create table
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
