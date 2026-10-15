import { useEffect, useRef, useCallback, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useConsoleStore } from '../stores'
import { postToIframe, isIframeMessage } from './bridge'
import type { IframeMessage } from './bridge'
import { SANDBOX_RUNTIME_CODE } from './sandbox-runtime'
import { bundleFiles } from './esbuild-bundler'
import type { AppFile } from './esbuild-bundler'
import { config } from '@/shared/lib/config'
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
  /** preview token for `/api/p/` calls (§5.2, C12) */
  getToken?: () => Promise<string | null>
  refreshToken?: () => Promise<string | null>
  onApiCall?: (call: BridgeCall) => void
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

/** Engine origin the preview's relative URLs resolve against (§11.4: `config.workflowApiUrl`). */
function engineBase(): string {
  return config.workflowApiUrl || config.engineOrigin
}

/**
 * Sandbox renderer — a sandboxed iframe with React, ReactDOM and Tailwind pre-loaded. Project
 * files are bundled with esbuild-wasm here and sent to the iframe via postMessage. Every
 * `fetch` from the app is proxied through this bridge (C12): relative URLs resolve against the
 * engine origin, and `/api/p/` calls to the engine get the preview token.
 */
export function IframeSandbox({ files, getToken, refreshToken, onApiCall }: IframeSandboxProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const pendingFilesRef = useRef<AppFile[] | null>(null)
  const lastHashRef = useRef<string | null>(null)
  const propsRef = useRef({ getToken, refreshToken, onApiCall })
  propsRef.current = { getToken, refreshToken, onApiCall }

  const log = useConsoleStore.getState().log

  const sendToIframe = useCallback(
    (appFiles: AppFile[]) => {
      const hash = hashFiles(appFiles)
      if (hash === lastHashRef.current) return
      lastHashRef.current = hash
      bundleFiles(appFiles)
        .then(({ code, css, errors }) => {
          if (errors.length > 0) {
            const message = errors.join('\n')
            setError(message)
            log('error', 'bundle', message)
            return
          }
          setError(null)
          if (iframeRef.current) postToIframe(iframeRef.current, { type: 'render', source: code, css: css || undefined })
        })
        .catch((err) => {
          const message = err instanceof Error ? err.message : 'Bundle failed'
          setError(message)
          log('error', 'bundle', message)
        })
    },
    [log],
  )

  const handleApiRequest = useCallback(async (reqId: string, url: string, opts: RequestInit) => {
    const win = iframeRef.current?.contentWindow
    if (!win) return
    const reply = (msg: Record<string, unknown>, transfer?: Transferable[]) =>
      win.postMessage({ type: 'apiResponse', reqId, ...msg }, '*', transfer ?? [])
    try {
      const resolved = new URL(url, engineBase())
      const headers = normaliseHeaders(opts?.headers)
      const hasAuth = Object.keys(headers).some((k) => k.toLowerCase() === 'authorization')
      const isPreviewApi = resolved.origin === config.engineOrigin && resolved.pathname.startsWith('/api/p/')
      const addToken = isPreviewApi && !hasAuth && !!propsRef.current.getToken
      const method = (opts?.method ?? 'GET').toUpperCase()
      const doFetch = async (token: string | null) =>
        fetch(resolved.toString(), {
          method,
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
      reply({ error: err instanceof Error ? err.message : 'Fetch failed' })
    }
  }, [])

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
          break
        case 'console':
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
    [sendToIframe, handleApiRequest, log],
  )

  useEffect(() => {
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [handleMessage])

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
              if (files) sendToIframe(files)
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

function buildIframeHTML(): string {
  // The iframe has an opaque origin (no allow-same-origin: a blob: document would otherwise share
  // the studio's origin and could reach the parent), so storage is shimmed in memory.
  // The Tailwind Play CDN prints a "should not be used in production" warning on load; the
  // preview is not production and E2E fails on any iframe warning (§12.3), so that one line is dropped.
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <script>(function(){function mem(){var d={};return{getItem:function(k){return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null},setItem:function(k,v){d[k]=String(v)},removeItem:function(k){delete d[k]},clear:function(){d={}},key:function(i){return Object.keys(d)[i]||null},get length(){return Object.keys(d).length}}};['localStorage','sessionStorage'].forEach(function(n){try{void window[n].length}catch(e){try{Object.defineProperty(window,n,{value:mem(),configurable:true})}catch(e2){}}})})();<\/script>
  <script>(function(){var w=console.warn;console.warn=function(){var a=arguments[0];if(typeof a==='string'&&a.indexOf('cdn.tailwindcss.com should not be used in production')!==-1)return;return w.apply(console,arguments)}})();<\/script>
  <script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"><\/script>
  <script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js"><\/script>
  <script src="https://cdn.tailwindcss.com"><\/script>
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
