/** Table ⋯ menu (UI-DESIGN §3.4): rename (`409 name_taken` inline) and delete with type-to-confirm. */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Ellipsis } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage, isApiError } from '@/shared/api/http'
import type { DataTable } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { pluralize } from '@/features/project/lib/format'
import { useDeleteTable, useUpdateTable } from '../useTableMutations'

export function TableMenu({ table, onColumns }: { table: DataTable; onColumns: () => void }) {
  const [dialog, setDialogState] = useState<'rename' | 'delete' | null>(null)
  // a fresh key per opening resets the dialog's form state
  const [opened, setOpened] = useState(0)
  const setDialog = (d: 'rename' | 'delete' | null) => {
    if (d) setOpened((n) => n + 1)
    setDialogState(d)
  }
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon-sm" aria-label={`${table.name} actions`} data-testid="table-menu">
              <Ellipsis />
            </Button>
          }
        />
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuItem onClick={() => setDialog('rename')} data-testid="table-rename">
            Rename table
          </DropdownMenuItem>
          <DropdownMenuItem onClick={onColumns}>Edit columns</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => setDialog('delete')} data-testid="table-delete">
            Delete table
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RenameDialog key={`r${opened}`} table={table} open={dialog === 'rename'} onClose={() => setDialog(null)} />
      <DeleteDialog key={`d${opened}`} table={table} open={dialog === 'delete'} onClose={() => setDialog(null)} />
    </>
  )
}

function RenameDialog({ table, open, onClose }: { table: DataTable; open: boolean; onClose: () => void }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const update = useUpdateTable(pid, {
    onUpdated: (t) => {
      onClose()
      navigate({ to: '/p/$project/data/$table', params: { project: slug, table: t.name }, replace: true })
    },
  })
  const [name, setName] = useState(table.name)
  const [error, setError] = useState<string | null>(null)
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md" data-testid="rename-table-dialog">
        <DialogHeader>
          <DialogTitle>Rename table</DialogTitle>
          <DialogDescription>
            Code that calls <span className="font-mono">studio.db.table("{table.name}")</span> must be updated too.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            setError(null)
            if (!name || name === table.name) return onClose()
            update.mutate(
              { id: table.id, body: { name } },
              {
                onError: (err) =>
                  setError(isApiError(err, 'name_taken') ? `A table called "${name}" already exists.` : errorMessage(err)),
              },
            )
          }}
        >
          <Field label="Name" error={error} errorTestId="rename-table-error">
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value.trim())}
              className="font-mono"
              aria-invalid={!!error}
              data-testid="rename-table-name"
            />
          </Field>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={!name || update.isPending} data-testid="rename-table-save">
              Rename
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DeleteDialog({ table, open, onClose }: { table: DataTable; open: boolean; onClose: () => void }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const del = useDeleteTable(pid, {
    onDeleted: () => {
      onClose()
      toast.success(`Deleted ${table.name}`)
      navigate({ to: '/p/$project/data', params: { project: slug }, replace: true })
    },
  })
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md" data-testid="delete-table-dialog">
        <DialogHeader>
          <DialogTitle>Delete {table.name}?</DialogTitle>
          <DialogDescription>
            This deletes the table and its {pluralize(table.row_count ?? 0, 'row')} for every environment. Routes that use it
            will fail with <span className="font-mono">table_not_found</span>.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (typed !== table.name) return
            del.mutate(table.id, { onError: (err) => setError(errorMessage(err)) })
          }}
        >
          <Field label={<>Type <span className="font-mono">{table.name}</span> to confirm</>} error={error}>
            <Input
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              className="font-mono"
              data-testid="delete-table-confirm-input"
            />
          </Field>
          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              variant="destructive"
              disabled={typed !== table.name || del.isPending}
              data-testid="delete-table-confirm"
            >
              Delete table
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
