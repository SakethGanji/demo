/**
 * Self-contained JavaScript that runs inside the sandboxed iframe.
 * Exported as a string constant to be inlined into the iframe HTML template.
 *
 * The iframe receives a pre-bundled IIFE from esbuild-wasm (bundled in the
 * parent window).  The bundle sets `window.__AppModule` with a `.default`
 * export that is the root React component.
 *
 * Responsibilities:
 * - Execute the bundle and mount the default-exported component
 * - Patch console.* to forward to parent
 * - Override fetch() to proxy ALL calls through parent (bypasses iframe CORS).
 *   Returns a real Response so .json() / .blob() / .arrayBuffer() all work,
 *   including for binary file downloads.
 * - ErrorBoundary for runtime errors
 */

export const SANDBOX_RUNTIME_CODE = `
(function() {
  'use strict';

  var React = window.React;
  var ReactDOM = window.ReactDOM;
  var createRoot = ReactDOM.createRoot;
  var root = null;
  var mountEl = document.getElementById('root');

  // ── Console patching ─────────────────────────────────────────────
  var origConsole = { log: console.log, info: console.info, warn: console.warn, error: console.error };
  ['log', 'info', 'warn', 'error'].forEach(function(level) {
    console[level] = function() {
      var args = Array.from(arguments).map(function(a) {
        try { return typeof a === 'object' ? JSON.parse(JSON.stringify(a)) : a; }
        catch(e) { return String(a); }
      });
      origConsole[level].apply(console, arguments);
      parent.postMessage({ type: 'console', level: level, args: args }, '*');
    };
  });

  // ── API fetch bridge ─────────────────────────────────────────────
  // All fetches are proxied through the parent so the generated app can call
  // any URL (relative or absolute) without hitting iframe CORS. The parent
  // returns the raw body bytes + headers; we reconstruct a real Response.
  var pendingRequests = new Map();
  var reqCounter = 0;

  function proxyFetch(url, opts) {
    return new Promise(function(resolve, reject) {
      var reqId = 'req_' + (++reqCounter) + '_' + Date.now();
      pendingRequests.set(reqId, { resolve: resolve, reject: reject });

      // postMessage can clone strings/arraybuffers but not Streams/FormData
      // reliably across browsers — keep the LLM-generated path on plain types.
      var safeOpts = opts ? {
        method: opts.method,
        headers: opts.headers,
        body: typeof opts.body === 'string' ? opts.body : undefined,
      } : {};

      parent.postMessage({
        type: 'apiRequest', reqId: reqId, url: url, opts: safeOpts
      }, '*');

      setTimeout(function() {
        if (pendingRequests.has(reqId)) {
          pendingRequests.delete(reqId);
          reject(new Error('API request timed out'));
        }
      }, 480000);
    });
  }

  // Override fetch — every call goes through the parent bridge.
  window.fetch = function(url, opts) {
    return proxyFetch(typeof url === 'string' ? url : String(url), opts);
  };

  // ── Error Boundary ───────────────────────────────────────────────
  class ErrorBoundary extends React.Component {
    constructor(props) {
      super(props);
      this.state = { error: null };
    }
    static getDerivedStateFromError(error) {
      return { error: error };
    }
    componentDidCatch(error, info) {
      parent.postMessage({
        type: 'error',
        message: error.message,
        stack: (info.componentStack || '') + '\\n' + (error.stack || '')
      }, '*');
    }
    render() {
      if (this.state.error) {
        return React.createElement('div', {
          style: {
            padding: '24px', color: '#ef4444', fontFamily: 'monospace',
            fontSize: '13px', whiteSpace: 'pre-wrap', lineHeight: '1.5'
          }
        },
          React.createElement('strong', null, 'Runtime Error'),
          '\\n\\n',
          this.state.error.message
        );
      }
      return this.props.children;
    }
  }

  // ── Render bundled code ───────────────────────────────────────────
  function renderSource(bundledCode) {
    try {
      window.__AppModule = undefined;

      // esbuild IIFE produces: var __AppModule = (()=>{...})();
      // new Function() scopes var locally, so we rewrite to window assignment
      var patchedCode = bundledCode.replace('var __AppModule', 'window.__AppModule');
      (new Function(patchedCode))();

      var mod = window.__AppModule;
      if (!mod || typeof mod.default !== 'function') {
        throw new Error(
          'App.tsx must export a default React component.\\n' +
          'Example: export default function App() { return <div>Hello</div> }'
        );
      }

      var Component = mod.default;

      if (!root) {
        root = createRoot(mountEl);
      }

      root.render(
        React.createElement(ErrorBoundary, { key: Date.now() },
          React.createElement(Component)
        )
      );

    } catch (err) {
      parent.postMessage({
        type: 'error',
        message: err.message,
        stack: err.stack || ''
      }, '*');

      if (!root) {
        root = createRoot(mountEl);
      }
      root.render(
        React.createElement('div', {
          style: {
            padding: '24px', color: '#ef4444', fontFamily: 'monospace',
            fontSize: '13px', whiteSpace: 'pre-wrap', lineHeight: '1.5'
          }
        },
          React.createElement('strong', null, 'Error'),
          '\\n\\n',
          err.message
        )
      );
    }
  }

  // ── Inject CSS (from esbuild CSS output) ─────────────────────────
  function injectCSS(css) {
    var existing = document.getElementById('__app-css');
    if (existing) existing.remove();
    if (!css) return;
    var style = document.createElement('style');
    style.id = '__app-css';
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ── Message Handler ──────────────────────────────────────────────
  window.addEventListener('message', function(event) {
    var data = event.data;
    if (!data || !data.type) return;

    if (data.type === 'render') {
      if (data.css) injectCSS(data.css);
      renderSource(data.source);
    } else if (data.type === 'apiResponse') {
      var pending = pendingRequests.get(data.reqId);
      if (pending) {
        pendingRequests.delete(data.reqId);
        if (data.error) {
          pending.reject(new Error(data.error));
        } else {
          var bodyInit = data.body == null ? null : data.body;
          var resp = new Response(bodyInit, {
            status: data.status || 200,
            statusText: data.statusText || '',
            headers: data.headers || {},
          });
          pending.resolve(resp);
        }
      }
    }
  });

  // ── Signal ready ─────────────────────────────────────────────────
  parent.postMessage({ type: 'ready' }, '*');
})();
`
