import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { projectsApi } from '@/shared/api/endpoints'
import { ApiError, errorMessage } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'

export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const create = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim(), description: description.trim() || undefined }),
    onSuccess: async (p) => {
      await invalidate.projectCreated(qc)
      onOpenChange(false)
      setName('')
      setDescription('')
      navigate({ to: '/p/$project', params: { project: p.slug } })
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : errorMessage(e)),
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" data-testid="new-project-dialog">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            setError(null)
            if (name.trim()) create.mutate()
          }}
        >
          <Field label="Name" htmlFor="np-name" error={error} errorTestId="new-project-error">
            <Input
              id="np-name"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Payments Ops"
              data-testid="new-project-name"
            />
          </Field>
          <Field label="Description" htmlFor="np-desc" help="Optional">
            <Textarea id="np-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
          </Field>
          <DialogFooter>
            <Button variant="outline" type="button" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || create.isPending} data-testid="new-project-submit">
              Create project
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
