import { useEffect, useRef, useCallback, useState } from 'react'
import { Loader2, AlertTriangle, RefreshCw } from 'lucide-react'
import { useConsoleStore } from '../stores'
import { postToIframe, isIframeMessage } from './bridge'
import type { IframeMessage } from './bridge'
import { SANDBOX_RUNTIME_CODE } from './sandbox-runtime'
import { bundleFiles } from './esbuild-bundler'
import type { AppFile } from './esbuild-bundler'
import { backends } from '@/shared/lib/config'

interface IframeSandboxProps {
  files: AppFile[] | null
  onError?: (error: { message: string; stack?: string }) => void
}

/**
 * Sandbox renderer — creates a sandboxed iframe with React, ReactDOM,
 * and Tailwind pre-loaded. Bundles project files with esbuild-wasm in the
 * parent window, then sends the compiled bundle to iframe via postMessage.
 */
/** Simple content hash to avoid redundant re-bundles. */
function hashFiles(files: AppFile[]): string {
  return files.map((f) => f.path + '\0' + f.content).join('\x01')
}

export function IframeSandbox({ files, onError }: IframeSandboxProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const pendingFilesRef = useRef<AppFile[] | null>(null)
  const blobUrlRef = useRef<string | null>(null)
  const lastHashRef = useRef<string | null>(null)

  const log = useConsoleStore.getState().log

  // Bundle files + send to iframe
  const sendToIframe = useCallback((appFiles: AppFile[]) => {
    // Skip if content hasn't changed
    const hash = hashFiles(appFiles)
    if (hash === lastHashRef.current) return
    lastHashRef.current = hash

    bundleFiles(appFiles)
      .then(({ code, css, errors }) => {
        if (errors.length > 0) {
          const message = errors.join('\n')
          setError(message)
          log('error', 'bundle', message)
          onError?.({ message })
          return
        }
        if (iframeRef.current) {
          postToIframe(iframeRef.current, { type: 'render', source: code, css: css || undefined })
        }
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : 'Bundle failed'
        setError(message)
        log('error', 'bundle', message)
        onError?.({ message })
      })
  }, [log, onError])

  // Handle messages from iframe
  const handleMessage = useCallback((event: MessageEvent) => {
    const data = event.data as unknown
    if (!isIframeMessage(data)) return

    const msg = data as IframeMessage

    switch (msg.type) {
      case 'ready':
        setReady(true)
        setLoading(false)
        if (pendingFilesRef.current) {
          sendToIframe(pendingFilesRef.current)
          pendingFilesRef.current = null
        }
        break

      case 'error':
        setError(msg.message)
        log('error', 'sandbox', msg.message, msg.stack)
        onError?.({ message: msg.message, stack: msg.stack })
        break

      case 'console':
        log(
          msg.level === 'log' ? 'info' : msg.level === 'error' ? 'error' : msg.level === 'warn' ? 'warn' : 'info',
          'app',
          msg.args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' '),
        )
        break

      case 'apiRequest':
        handleApiRequest(msg.reqId, msg.url, msg.opts)
        break

      case 'resize':
        break
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onError, sendToIframe])

  // API bridge: proxy requests from iframe
  const handleApiRequest = useCallback(async (reqId: string, url: string, opts: RequestInit) => {
    const iframe = iframeRef.current
    if (!iframe?.contentWindow) return

    try {
      const fullUrl = url.startsWith('http') ? url : (backends.workflow || window.location.origin) + url
      const response = await fetch(fullUrl, {
        ...opts,
        headers: {
          'Content-Type': 'application/json',
          ...(opts.headers as Record<string, string> || {}),
        },
      })

      const contentType = response.headers.get('content-type') || ''
      const result = contentType.includes('json') ? await response.json() : await response.text()

      iframe.contentWindow.postMessage({ type: 'apiResponse', reqId, result }, '*')
    } catch (err) {
      iframe.contentWindow.postMessage(
        { type: 'apiResponse', reqId, error: err instanceof Error ? err.message : 'Fetch failed' },
        '*',
      )
    }
  }, [])

  // Create blob URL for iframe on mount
  useEffect(() => {
    const html = buildIframeHTML()
    const blob = new Blob([html], { type: 'text/html' })
    const url = URL.createObjectURL(blob)
    blobUrlRef.current = url

    window.addEventListener('message', handleMessage)

    return () => {
      window.removeEventListener('message', handleMessage)
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current)
        blobUrlRef.current = null
      }
    }
  }, [handleMessage])

  // Send files to iframe when they change
  useEffect(() => {
    if (!files || files.length === 0) return

    setError(null)

    if (ready) {
      sendToIframe(files)
    } else {
      pendingFilesRef.current = files
    }
  }, [files, ready, sendToIframe])

  if (!files || files.length === 0) {
    return null
  }

  return (
    <div className="relative h-full w-full">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background z-10">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="absolute top-0 left-0 right-0 z-20 flex items-center gap-2 px-3 py-2 bg-destructive/10 border-b border-destructive/20 text-destructive text-xs">
          <AlertTriangle size={12} />
          <span className="flex-1 truncate">{error}</span>
          <button
            onClick={() => {
              setError(null)
              lastHashRef.current = null // force re-bundle on retry
              if (files) sendToIframe(files)
            }}
            className="shrink-0 p-0.5 hover:bg-destructive/10 rounded"
            title="Retry"
          >
            <RefreshCw size={11} />
          </button>
        </div>
      )}

      <iframe
        ref={iframeRef}
        src={blobUrlRef.current || undefined}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
        className="w-full h-full border-0"
        title="App Preview"
      />
    </div>
  )
}

// ── HTML Template ──────────────────────────────────────────────────────────

function buildIframeHTML(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"><\/script>
  <script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js"><\/script>
  <script src="https://cdn.tailwindcss.com"><\/script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; padding: 0; font-family: ui-sans-serif, system-ui, sans-serif; }
    #root { min-height: 100vh; }
  </style>
</head>
<body>
  <div id="root"></div>
  <script>${SANDBOX_RUNTIME_CODE}<\/script>
</body>
</html>`
}
