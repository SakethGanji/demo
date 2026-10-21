import { useEffect, useRef, useCallback, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useConsoleStore } from '../stores'
import { postToIframe, isIframeMessage } from './bridge'
import type { IframeMessage } from './bridge'
import { SANDBOX_RUNTIME_CODE } from './sandbox-runtime'
import { bundleFiles } from './esbuild-bundler'
import type { AppFile } from './esbuild-bundler'
import { config } from '@/shared/lib/config'
import { previewRequestUrl, withViewAs, type PreviewRoute, type PreviewUser } from './requestPolicy'
import reactRuntime from '../../../../node_modules/react/umd/react.production.min.js?raw'
import reactDomRuntime from '../../../../node_modules/react-dom/umd/react-dom.production.min.js?raw'
import tailwindRuntime from '@tailwindcss/browser?raw'
import { PreviewErrorCollector, apiFailure, isAccessRefusal, type PreviewError } from '../lib/previewErrors'
import { Spinner } from '@/shared/components/ui/spinner'

export interface BridgeCall {
  method: string
  /** path relative to the engine origin, e.g. `/api/p/payments-ops/refunds/stats` */
  path: string
  status: number
  ms: number
}

interface IframeSandboxProps {
  files: AppFile[] | null
  projectSlug: string
  allowedRoutes: PreviewRoute[]
  workflowPaths: string[]
  /** preview token for `/api/p/` calls (§5.2, C12) */
  getToken?: () => Promise<string | null>
  refreshToken?: () => Promise<string | null>
  onApiCall?: (call: BridgeCall) => void
  /** "View as": sent as X-Preview-User* headers on every preview request (null = the app itself) */
  viewAs?: PreviewUser | null
  /** runtime errors seen after a load, deduped and settled (~3s); `[]` when a new load starts */
  onPreviewErrors?: (errors: PreviewError[]) => void
}

/** Simple content hash to avoid redundant re-bundles. */
function hashFiles(files: AppFile[]): string {
  return files.map((f) => f.path + '\0' + f.content).join('\x01')
}

/** Headers from the iframe may be a plain object, an entries array, or (cloned) nothing (F14). */
export function normaliseHeaders(h: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (!h) return out
  if (Array.isArray(h)) {
    for (const pair of h) if (Array.isArray(pair) && pair.length >= 2) out[String(pair[0])] = String(pair[1])
    return out
  }
  if (typeof h === 'object') {
    for (const [k, v] of Object.entries(h as Record<string, unknown>)) if (v != null) out[k] = String(v)
  }
  return out
}

/**
 * Sandbox renderer — a sandboxed iframe with React, ReactDOM and Tailwind pre-loaded. Project
 * files are bundled with esbuild-wasm here and sent to the iframe via postMessage. Every
 * `fetch` from the app is proxied through this bridge (C12): relative URLs resolve against the
 * engine origin. Only selected project routes and bound workflow runs are permitted.
 */
export function IframeSandbox({ files, projectSlug, allowedRoutes, workflowPaths, getToken, refreshToken, onApiCall, viewAs, onPreviewErrors }: IframeSandboxProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const pendingFilesRef = useRef<AppFile[] | null>(null)
  const lastHashRef = useRef<string | null>(null)
  const generation = useRef(0)
  const propsRef = useRef({ projectSlug, allowedRoutes, workflowPaths, getToken, refreshToken, onApiCall, viewAs })
  propsRef.current = { projectSlug, allowedRoutes, workflowPaths, getToken, refreshToken, onApiCall, viewAs }
  useEffect(() => () => { generation.current++ }, [])

  const errorsCbRef = useRef(onPreviewErrors)
  errorsCbRef.current = onPreviewErrors
  const [collector] = useState(() => new PreviewErrorCollector((errs) => errorsCbRef.current?.(errs)))
  useEffect(() => () => {
    collector.dispose()
    errorsCbRef.current?.([])
  }, [collector])

  const log = useConsoleStore.getState().log

  const sendToIframe = useCallback(
    (appFiles: AppFile[]) => {
      const hash = hashFiles(appFiles)
      if (hash === lastHashRef.current) return
      lastHashRef.current = hash
      const current = ++generation.current
      bundleFiles(appFiles)
        .then(({ code, css, errors }) => {
          if (current !== generation.current) return
          if (errors.length > 0) {
            const message = errors.join('\n')
            setError(message)
            log('error', 'bundle', message)
            return
          }
          setError(null)
          collector.reset()
          if (iframeRef.current) postToIframe(iframeRef.current, { type: 'render', source: code, css: css || undefined })
        })
        .catch((err) => {
          if (current !== generation.current) return
          const message = err instanceof Error ? err.message : 'Bundle failed'
          setError(message)
          log('error', 'bundle', message)
        })
    },
    [log, collector],
  )

  const handleApiRequest = useCallback(async (reqId: string, url: string, opts: RequestInit) => {
    const win = iframeRef.current?.contentWindow
    if (!win) return
    const reply = (msg: Record<string, unknown>, transfer?: Transferable[]) =>
      win.postMessage({ type: 'apiResponse', reqId, ...msg }, '*', transfer ?? [])
    try {
      const method = (opts?.method ?? 'GET').toUpperCase()
      const resolved = previewRequestUrl(url, method, config.engineOrigin, propsRef.current.projectSlug, propsRef.current.allowedRoutes, propsRef.current.workflowPaths, 'preview')
      const headers = withViewAs(normaliseHeaders(opts?.headers), propsRef.current.viewAs)
      const hasAuth = Object.keys(headers).some((k) => k.toLowerCase() === 'authorization')
      const isPreviewApi = resolved.origin === config.engineOrigin && resolved.pathname.startsWith('/api/p/')
      const addToken = !hasAuth && !!propsRef.current.getToken
      const doFetch = async (token: string | null) =>
        fetch(resolved.toString(), {
          method,
          credentials: 'omit',
          redirect: 'error',
          headers: token ? { ...headers, Authorization: `Bearer ${token}` } : headers,
          body: typeof opts?.body === 'string' && method !== 'GET' && method !== 'HEAD' ? opts.body : undefined,
        })
      const started = performance.now()
      let response = await doFetch(addToken ? await propsRef.current.getToken!() : null)
      let body = await response.arrayBuffer()
      if (addToken && response.status === 401 && propsRef.current.refreshToken) {
        let code: unknown = null
        try {
          const j = JSON.parse(new TextDecoder().decode(body))
          code = j?.error ?? j?.detail?.code
        } catch {
          /* not JSON */
        }
        if (code === 'token_expired') {
          const fresh = await propsRef.current.refreshToken()
          response = await doFetch(fresh)
          body = await response.arrayBuffer()
        }
      }
      if (isPreviewApi && response.status >= 400 && !isAccessRefusal(response.status)) {
        collector.add(apiFailure(method, resolved.pathname, response.status))
      }
      if (isPreviewApi) {
        propsRef.current.onApiCall?.({
          method,
          path: resolved.pathname + resolved.search,
          status: response.status,
          ms: Math.round(performance.now() - started),
        })
      }
      const outHeaders: Record<string, string> = {}
      response.headers.forEach((v, k) => {
        outHeaders[k] = v
      })
      reply({ status: response.status, statusText: response.statusText, headers: outHeaders, body }, [body])
    } catch (err) {
      const why = err instanceof Error ? err.message : 'Fetch failed'
      collector.add({ kind: 'api', message: `${(opts?.method ?? 'GET').toUpperCase()} ${String(url).split('?')[0]} failed: ${why}` })
      reply({ error: why })
    }
  }, [collector])

  // Messages: only from our own iframe (F12).
  const handleMessage = useCallback(
    (event: MessageEvent) => {
      if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return
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
          log('error', 'sandbox', msg.message)
          collector.add({ kind: 'render', message: msg.message })
          break
        case 'runtimeError':
          log('error', 'sandbox', msg.message)
          collector.add({ kind: 'uncaught', message: msg.message })
          break
        case 'console':
          if (msg.level === 'error') {
            collector.add({ kind: 'console', message: msg.args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ') })
          }
          log(
            msg.level === 'error' ? 'error' : msg.level === 'warn' ? 'warn' : 'info',
            'app',
            msg.args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' '),
          )
          break
        case 'apiRequest':
          void handleApiRequest(msg.reqId, msg.url, msg.opts)
          break
      }
    },
    [sendToIframe, handleApiRequest, log, collector],
  )

  useEffect(() => {
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [handleMessage])

  useEffect(() => {
    if (ready || !blobUrl) return
    const timeout = setTimeout(() => { setLoading(false); setError('Preview could not start. Retry to reload its runtime.') }, 15000)
    return () => clearTimeout(timeout)
  }, [ready, blobUrl])

  // One blob document per mount.
  useEffect(() => {
    const url = URL.createObjectURL(new Blob([buildIframeHTML()], { type: 'text/html' }))
    setBlobUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [])

  useEffect(() => {
    if (!files || files.length === 0) return
    if (ready) sendToIframe(files)
    else pendingFilesRef.current = files
  }, [files, ready, sendToIframe])

  if (!files || files.length === 0) return null

  return (
    <div className="relative h-full w-full">
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background">
          <Spinner className="text-muted-foreground" />
        </div>
      )}
      {error && (
        <div
          className="absolute inset-x-0 top-0 z-20 flex items-center gap-2 border-b border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          data-testid="preview-error"
        >
          <AlertTriangle className="size-3 shrink-0" />
          <span className="flex-1 truncate">{error}</span>
          <button
            type="button"
            onClick={() => {
              setError(null)
              lastHashRef.current = null
              if (!ready) {
                const next = URL.createObjectURL(new Blob([buildIframeHTML()], { type: 'text/html' }))
                if (blobUrl) URL.revokeObjectURL(blobUrl)
                setLoading(true)
                setBlobUrl(next)
              } else if (files) sendToIframe(files)
            }}
            className="shrink-0 rounded p-0.5 hover:bg-destructive/10"
            aria-label="Retry"
          >
            <RefreshCw className="size-3" />
          </button>
        </div>
      )}
      {blobUrl && (
        <iframe
          ref={iframeRef}
          src={blobUrl}
          sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads allow-pointer-lock allow-orientation-lock allow-presentation allow-top-navigation-by-user-activation"
          allow="clipboard-read; clipboard-write; fullscreen; autoplay; encrypted-media; picture-in-picture; geolocation; camera; microphone; display-capture; accelerometer; gyroscope; magnetometer"
          className="h-full w-full border-0 bg-white"
          title="App Preview"
          data-testid="preview-iframe"
        />
      )}
    </div>
  )
}

// ── HTML Template ──────────────────────────────────────────────────────────

function inlineScript(source: string): string { return source.replace(/<\/script/gi, '<\\/script') }

function buildIframeHTML(): string {
  // The iframe has an opaque origin (no allow-same-origin: a blob: document would otherwise share
  // the studio's origin and could reach the parent), so storage is shimmed in memory.
  // Dependencies are packaged with Studio; the preview never needs public script CDNs.
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <script>(function(){function mem(){var d={};return{getItem:function(k){return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null},setItem:function(k,v){d[k]=String(v)},removeItem:function(k){delete d[k]},clear:function(){d={}},key:function(i){return Object.keys(d)[i]||null},get length(){return Object.keys(d).length}}};['localStorage','sessionStorage'].forEach(function(n){try{void window[n].length}catch(e){try{Object.defineProperty(window,n,{value:mem(),configurable:true})}catch(e2){}}})})();<\/script>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob: https:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'" />
  <script>${inlineScript(reactRuntime)}<\/script>
  <script>${inlineScript(reactDomRuntime)}<\/script>
  <script>${inlineScript(tailwindRuntime)}<\/script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0 !important; padding: 0 !important; height: 100% !important; overflow: auto; }
    body { font-family: ui-sans-serif, system-ui, sans-serif; background: #fff; }
    #root { min-height: 100%; }
  </style>
</head>
<body>
  <div id="root"></div>
  <script>${SANDBOX_RUNTIME_CODE}<\/script>
</body>
</html>`
}
