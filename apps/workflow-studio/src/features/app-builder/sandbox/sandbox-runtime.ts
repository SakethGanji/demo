/**
 * Self-contained JavaScript that runs inside the sandboxed iframe.
 * Exported as a string constant to be inlined into the iframe HTML template.
 *
 * Responsibilities:
 * - Listen for `render(source)` messages from parent (pre-compiled JS, not TSX)
 * - Render the default-exported component via createRoot()
 * - Patch console.* to forward to parent
 * - Provide window.__apiFetch() for proxied API calls
 * - ErrorBoundary for runtime errors
 * - ResizeObserver to report content height
 *
 * Note: Transpilation (sucrase) happens in the PARENT window, not here.
 * The iframe receives already-compiled JS code.
 */

export const SANDBOX_RUNTIME_CODE = `
(function() {
  'use strict';

  const React = window.React;
  const ReactDOM = window.ReactDOM;
  const createRoot = ReactDOM.createRoot;
  let root = null;
  const mountEl = document.getElementById('root');

  // ── Console patching ─────────────────────────────────────────────
  const origConsole = { log: console.log, info: console.info, warn: console.warn, error: console.error };
  ['log', 'info', 'warn', 'error'].forEach(function(level) {
    console[level] = function() {
      const args = Array.from(arguments).map(function(a) {
        try { return typeof a === 'object' ? JSON.parse(JSON.stringify(a)) : a; }
        catch(e) { return String(a); }
      });
      origConsole[level].apply(console, arguments);
      parent.postMessage({ type: 'console', level: level, args: args }, '*');
    };
  });

  // ── API fetch bridge ─────────────────────────────────────────────
  const pendingRequests = new Map();
  let reqCounter = 0;

  window.__apiFetch = function(url, opts) {
    return new Promise(function(resolve, reject) {
      const reqId = 'req_' + (++reqCounter) + '_' + Date.now();
      pendingRequests.set(reqId, { resolve: resolve, reject: reject });
      parent.postMessage({
        type: 'apiRequest',
        reqId: reqId,
        url: url,
        opts: opts || {}
      }, '*');

      // Timeout after 8 minutes (POC — workflows with LLM calls can be slow)
      setTimeout(function() {
        if (pendingRequests.has(reqId)) {
          pendingRequests.delete(reqId);
          reject(new Error('API request timed out'));
        }
      }, 480000);
    });
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

  // ── Render pre-compiled JS ───────────────────────────────────────
  function renderSource(compiledCode) {
    try {
      // The parent has already transpiled TSX → JS and handled
      // export default → __DefaultExport__ conversion.
      // We just wrap it to inject React hooks as locals and eval.
      var wrappedCode =
        '(function(React, useState, useEffect, useCallback, useMemo, useRef, useReducer) {\\n' +
        compiledCode + '\\n' +
        'return __DefaultExport__;\\n' +
        '})(React, React.useState, React.useEffect, React.useCallback, React.useMemo, React.useRef, React.useReducer)';

      var Component = eval(wrappedCode);

      if (typeof Component !== 'function') {
        throw new Error('Module did not export a React component. Make sure you have "export default function ComponentName() { ... }"');
      }

      // Reset error boundary by remounting with new key
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

      // Show error in iframe
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

  // ── ResizeObserver ───────────────────────────────────────────────
  var resizeObserver = new ResizeObserver(function(entries) {
    for (var i = 0; i < entries.length; i++) {
      var height = entries[i].contentRect.height;
      parent.postMessage({ type: 'resize', height: height }, '*');
    }
  });
  resizeObserver.observe(mountEl);

  // ── Message Handler ──────────────────────────────────────────────
  window.addEventListener('message', function(event) {
    var data = event.data;
    if (!data || !data.type) return;

    if (data.type === 'render') {
      renderSource(data.source);
    } else if (data.type === 'themeUpdate') {
      var vars = data.vars;
      var rootEl = document.documentElement;
      for (var key in vars) {
        rootEl.style.setProperty('--' + key, vars[key]);
      }
    } else if (data.type === 'apiResponse') {
      var pending = pendingRequests.get(data.reqId);
      if (pending) {
        pendingRequests.delete(data.reqId);
        if (data.error) {
          pending.reject(new Error(data.error));
        } else {
          pending.resolve(data.result);
        }
      }
    }
  });

  // ── Signal ready ─────────────────────────────────────────────────
  parent.postMessage({ type: 'ready' }, '*');
})();
`
