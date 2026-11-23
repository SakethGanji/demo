// Northwind "offline mode" service worker: replays a queued payment from the worker itself.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('message', async (e) => {
  if (e.data?.type !== 'flush') return;
  const port = e.ports[0];
  try {
    const r = await fetch('/api/v1/payments', {
      method: 'POST', credentials: 'include',
      headers: { 'content-type': 'application/json', 'x-csrf-token': e.data.csrf, 'idempotency-key': e.data.key },
      body: JSON.stringify(e.data.payment),
    });
    port?.postMessage(`HTTP ${r.status}`);
  } catch (err) {
    port?.postMessage(`failed: ${err.message}`);
  }
});
