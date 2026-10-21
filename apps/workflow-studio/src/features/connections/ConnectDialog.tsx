import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Button } from '@/shared/components/ui/button'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { errorMessage } from '@/shared/api/http'
import { canConnect } from './connectionsLogic'
import { useConnectionMutations } from './useConnections'

/** "Connect a database": name + connection URL (masked). Postgres only for now. */
export function ConnectDialog({ pid, open, onClose }: { pid: string; open: boolean; onClose: () => void }) {
  const { create } = useConnectionMutations(pid)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    setName('')
    setUrl('')
    setError(null)
    create.reset()
    onClose()
  }
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    create.mutate(
      { name: name.trim(), kind: 'postgres', url: url.trim() },
      { onSuccess: close, onError: (err) => setError(errorMessage(err)) },
    )
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-[480px]" data-testid="connect-dialog">
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Connect a database</DialogTitle>
            <DialogDescription>
              Your apps and workflows can read these tables, and the plan can start from them. The URL is stored as a secret and never shown again.
            </DialogDescription>
          </DialogHeader>
          <Field label="Name" htmlFor="conn-name">
            <Input id="conn-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Sales warehouse" autoFocus data-testid="connect-name" />
          </Field>
          <Field label="Kind" help="Postgres is the only kind for now.">
            <Input value="Postgres" readOnly disabled data-testid="connect-kind" />
          </Field>
          <Field label="Connection URL" htmlFor="conn-url" error={error} errorTestId="connect-error">
            <Input
              id="conn-url"
              type="password"
              autoComplete="off"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="postgresql://user:password@host:5432/database"
              className="font-mono"
              data-testid="connect-url"
            />
          </Field>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canConnect(name, url) || create.isPending} data-testid="connect-submit">
              {create.isPending ? 'Connecting…' : 'Connect'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
