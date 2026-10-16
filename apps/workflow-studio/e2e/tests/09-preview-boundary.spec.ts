import { test, expect } from '../fixtures/test'
import { seedApp, seedProject, seedRefunds } from '../fixtures/seed'

test('preview blocks management, cross-project and external fetches while selected routes work offline from CDNs', async ({ page, api, s }) => {
  const p = await seedProject(api, 'boundary')
  const other = await seedProject(api, 'other')
  const routes = await seedRefunds(api, p)
  const external: string[] = []
  await page.route(/https:\/\/(cdn\.jsdelivr\.net|cdn\.tailwindcss\.com)/, async (route) => {
    external.push(route.request().url())
    await route.abort()
  })
  const app = await seedApp(api, p, 'Boundary', [{ path: 'App.tsx', content: `
import React from 'react';
export default function App() {
  const [result, setResult] = React.useState('Loading');
  React.useEffect(() => { (async () => {
    const blocked = [];
    for (const url of ['/api/projects/${other.id}', '/api/p/${other.slug}/refunds/stats', 'https://example.com/data']) {
      try { await fetch(url, {method:'DELETE'}); blocked.push(false); } catch { blocked.push(true); }
    }
    const response = await fetch('/api/p/${p.slug}/refunds/stats');
    setResult(blocked.every(Boolean) && response.ok ? 'Boundary verified' : 'Boundary failed');
  })() }, []);
  return <div className="bg-blue-600 p-4 text-white" data-testid="boundary-result">{result}</div>;
}` }], [routes.routeId('refundStats')])
  await page.goto(`${s.studioUrl}/p/${p.slug}/apps/${app.id}`)
  const result = page.frameLocator('[data-testid="preview-iframe"]').getByTestId('boundary-result')
  await expect(result).toHaveText('Boundary verified')
  await expect(result).toHaveCSS('background-color', 'oklch(0.546 0.245 262.881)')
  expect(external).toEqual([])
  expect((await api.get<{ id: string }>(`/projects/${other.id}`)).id).toBe(other.id)
})
