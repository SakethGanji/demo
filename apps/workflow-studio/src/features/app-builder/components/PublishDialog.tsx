import { useEffect, useState, useCallback } from 'react'
import { X, Loader2, Globe, Lock, KeyRound, Copy, CheckCheck, ExternalLink, PowerOff } from 'lucide-react'
import { toast } from 'sonner'
import { appsApi, type ApiAppDetail, type ApiAppAccess } from '@/shared/lib/api'
import { useAppDocumentStore } from '../stores'

interface PublishDialogProps {
  appId: string
  app: ApiAppDetail
  onClose: () => void
  onPublished: (updated: ApiAppDetail) => void
}

const ACCESS_OPTIONS: { value: ApiAppAccess; icon: typeof Globe; label: string; help: string }[] = [
  { value: 'public', icon: Globe, label: 'Public', help: 'Anyone with the link can view.' },
  { value: 'password', icon: KeyRound, label: 'Password', help: 'Visitors enter a password to view.' },
  { value: 'private', icon: Lock, label: 'Private', help: 'Only signed-in admins. URL returns 404 publicly.' },
]

export function PublishDialog({ appId, app, onClose, onPublished }: PublishDialogProps) {
  // Local form state seeded from the app. The dialog drives a single PUT
  // (to update slug/access/password) before calling POST /publish — keeps
  // the publish endpoint clean and lets the user preview the slug they
  // just typed without an extra round-trip.
  const [slug, setSlug] = useState(app.slug ?? '')
  const [access, setAccess] = useState<ApiAppAccess>(app.access)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [publishedUrl, setPublishedUrl] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // Derive a slug suggestion from the app name when the input is empty.
  useEffect(() => {
    if (!slug && app.name) {
      const suggested = app.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63)
      setSlug(suggested)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app.name])

  const handlePublish = useCallback(async () => {
    setBusy(true)
    try {
      const result = await appsApi.publish(appId, {
        slug: slug || undefined,
        access,
        access_password: access === 'password' && password ? password : undefined,
      })
      const refreshed = await appsApi.get(appId)
      onPublished(refreshed)
      // Keep the document store's currentVersion in sync — the engine may
      // have promoted current_version_id (e.g. when snapshotting a draft as
      // a publish-triggered version). Without this the toolbar shows stale
      // data like "editing v1 · live v2".
      if (refreshed.current_version) {
        useAppDocumentStore.getState().setCurrentVersion(refreshed.current_version)
      }
      setPublishedUrl(result.public_url)
      toast.success('App published')
    } catch (err) {
      toast.error('Publish failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      })
    } finally {
      setBusy(false)
    }
  }, [appId, slug, access, password, onPublished])

  const handleCopy = useCallback(() => {
    if (!publishedUrl) return
    navigator.clipboard.writeText(publishedUrl).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [publishedUrl])

  const handleUnpublish = useCallback(async () => {
    if (!window.confirm('Take this app offline? The bundle is preserved — you can republish anytime.')) return
    setBusy(true)
    try {
      await appsApi.unpublish(appId)
      const refreshed = await appsApi.get(appId)
      onPublished(refreshed)
      if (refreshed.current_version) {
        useAppDocumentStore.getState().setCurrentVersion(refreshed.current_version)
      }
      toast.success('App unpublished')
      onClose()
    } catch (err) {
      toast.error('Unpublish failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      })
    } finally {
      setBusy(false)
    }
  }, [appId, onPublished, onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[460px] max-w-[92vw] rounded-xl bg-card border border-border shadow-2xl p-5"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-foreground">Publish app</h2>
          <button
            onClick={onClose}
            className="h-7 w-7 rounded-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <X size={14} />
          </button>
        </div>

        {publishedUrl ? (
          // Success state — URL with copy + open buttons.
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Your app is live.</p>
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/30 px-2 py-1.5">
              <code className="flex-1 truncate text-xs font-mono text-foreground">{publishedUrl}</code>
              <button
                onClick={handleCopy}
                className="shrink-0 h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                title="Copy URL"
              >
                {copied ? <CheckCheck size={13} /> : <Copy size={13} />}
              </button>
              <a
                href={publishedUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="shrink-0 h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                title="Open"
              >
                <ExternalLink size={13} />
              </a>
            </div>
            <div className="flex justify-end">
              <button
                onClick={onClose}
                className="px-3 py-1.5 rounded-md bg-accent text-foreground text-xs font-medium hover:bg-accent/80 transition-colors"
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          // Form state.
          <div className="space-y-4">
            {/* Slug */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">URL</label>
              <div className="flex items-center rounded-md border border-border bg-background overflow-hidden focus-within:ring-1 focus-within:ring-ring">
                <span className="text-xs text-muted-foreground px-2 py-1.5 bg-muted/40 border-r border-border whitespace-nowrap">
                  /a/
                </span>
                <input
                  type="text"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                  placeholder="my-app"
                  maxLength={63}
                  className="flex-1 bg-transparent px-2 py-1.5 text-xs font-mono outline-none"
                />
              </div>
              <p className="text-[11px] text-muted-foreground">3–63 characters, lowercase letters, numbers, hyphens.</p>
            </div>

            {/* Access mode */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">Access</label>
              <div className="grid grid-cols-3 gap-1.5">
                {ACCESS_OPTIONS.map((opt) => {
                  const Icon = opt.icon
                  const selected = access === opt.value
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setAccess(opt.value)}
                      className={`flex flex-col items-center gap-1 px-2 py-2 rounded-md border text-[11px] transition-colors ${
                        selected
                          ? 'border-primary bg-primary/10 text-primary'
                          : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent'
                      }`}
                    >
                      <Icon size={14} />
                      <span className="font-medium">{opt.label}</span>
                    </button>
                  )
                })}
              </div>
              <p className="text-[11px] text-muted-foreground">
                {ACCESS_OPTIONS.find((o) => o.value === access)?.help}
              </p>
            </div>

            {/* Password input — only when needed. Existing password persists if input is left empty. */}
            {access === 'password' && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">
                  Password {app.access_password_set && <span className="text-muted-foreground font-normal">(leave blank to keep current)</span>}
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={app.access_password_set ? '••••••••' : 'Set a password'}
                  className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
            )}

            {/* Actions. Unpublish lives on the left so it doesn't sit next to
                the primary action — destructive-ish, easy to find, hard to
                hit by accident. */}
            <div className="flex items-center justify-between gap-2 pt-2">
              <div>
                {app.active && (
                  <button
                    onClick={handleUnpublish}
                    disabled={busy}
                    className="px-3 py-1.5 rounded-md text-xs text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50 flex items-center gap-1.5"
                  >
                    <PowerOff size={12} />
                    Unpublish
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2">
              <button
                onClick={onClose}
                disabled={busy}
                className="px-3 py-1.5 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handlePublish}
                disabled={busy || (access === 'password' && !password && !app.access_password_set)}
                className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 flex items-center gap-1.5"
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Globe size={12} />}
                Publish
              </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
