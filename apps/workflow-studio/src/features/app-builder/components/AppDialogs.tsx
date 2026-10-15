/**
 * §6.2 Delete app and §6.3 Unpublish confirmations (AlertDialog), shared by the app switcher,
 * the publish success state and the App settings sheet.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { appsApi, type ApiAppDetail } from '@/shared/lib/api'
import { errorMessage } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { useAppDocumentStore } from '../stores'

export function DeleteAppDialog({
  app,
  pid,
  projectSlug,
  open,
  onOpenChange,
}: {
  app: Pick<ApiAppDetail, 'id' | 'name' | 'slug' | 'active'>
  pid: string
  projectSlug: string
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <AlertDialog open={open} onOpenChange={(o) => { if (!o) setError(null); onOpenChange(o) }}>
      <AlertDialogContent data-testid="app-delete-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {app.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            {app.slug && app.active ? (
              <>
                Its published link <span className="font-mono">/a/{app.slug}</span> stops working.
              </>
            ) : (
              'Its versions and chat history are deleted. This cannot be undone.'
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <div className="text-[11.5px] text-destructive" role="alert" data-testid="app-delete-error">
            {error}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            data-testid="app-delete-confirm"
            onClick={async () => {
              setBusy(true)
              setError(null)
              try {
                await appsApi.delete(app.id)
                qc.removeQueries({ queryKey: qk.app(app.id) })
                await invalidate.appsChanged(qc, pid)
                onOpenChange(false)
                toast.success(`Deleted ${app.name}`)
                navigate({ to: '/p/$project', params: { project: projectSlug } })
              } catch (e) {
                setError(errorMessage(e))
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? 'Deleting…' : 'Delete app'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function UnpublishDialog({
  app,
  pid,
  open,
  onOpenChange,
  onDone,
}: {
  app: Pick<ApiAppDetail, 'id' | 'slug'>
  pid: string
  open: boolean
  onOpenChange: (o: boolean) => void
  onDone?: () => void
}) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <AlertDialog open={open} onOpenChange={(o) => { if (!o) setError(null); onOpenChange(o) }}>
      <AlertDialogContent data-testid="app-unpublish-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>Unpublish?</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-mono">/a/{app.slug}</span> will return 404. The app and its versions are kept; you can publish again.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <div className="text-[11.5px] text-destructive" role="alert" data-testid="app-unpublish-error">
            {error}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            data-testid="app-unpublish-confirm"
            onClick={async () => {
              setBusy(true)
              setError(null)
              try {
                await appsApi.unpublish(app.id)
                const fresh = await appsApi.get(app.id)
                qc.setQueryData(qk.app(app.id), fresh)
                if (fresh.current_version) useAppDocumentStore.getState().setCurrentVersion(fresh.current_version)
                await invalidate.appPublished(qc, pid, app.id)
                onOpenChange(false)
                toast.success('Unpublished')
                onDone?.()
              } catch (e) {
                setError(errorMessage(e))
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? 'Unpublishing…' : 'Unpublish'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
