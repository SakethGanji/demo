import { describe, expect, it } from 'vitest'
import { bundleFiles } from './esbuild-bundler'
import { withViewAs } from './requestPolicy'

describe('css imports', () => {
  it('bundles a relative css import into the css output', async () => {
    const r = await bundleFiles([
      { path: 'App.tsx', content: "import './index.css'\nimport { x } from './lib/x'\nexport default function App() { return x }" },
      { path: 'lib/x.ts', content: "import '../styles/more.css'\nexport const x = 1" },
      { path: 'index.css', content: ':root { --brand: #123456; }' },
      { path: 'styles/more.css', content: '.card { color: var(--brand); }' },
    ])
    expect(r.errors).toEqual([])
    expect(r.css).toContain('--brand')
    expect(r.css).toContain('.card')
    expect(r.code).not.toContain('index.css')
  })
  it('leaves the tailwind import for the preview runtime', async () => {
    const r = await bundleFiles([
      { path: 'src/App.tsx', content: "import './index.css'\nexport default () => null" },
      { path: 'src/index.css', content: '@import "tailwindcss";\n@theme { --color-brand: red; }' },
    ])
    expect(r.errors).toEqual([])
    expect(r.css).toContain('tailwindcss')
    expect(r.css).toContain('--color-brand')
  })
  it('reports a missing css file', async () => {
    const r = await bundleFiles([{ path: 'App.tsx', content: "import './nope.css'\nexport default () => null" }])
    expect(r.errors.join()).toContain('nope.css')
  })
})

describe('view-as header injection', () => {
  it('replaces app-set identity headers with the chosen user', () => {
    const out = withViewAs({ 'content-type': 'application/json', 'x-preview-user': 'mallory' }, { id: 'alice', name: 'Alice', roles: ['staff'] })
    expect(out).toEqual({ 'content-type': 'application/json', 'X-Preview-User': 'alice', 'X-Preview-User-Name': 'Alice', 'X-Preview-User-Roles': 'staff' })
  })
  it('strips app-set identity headers when no user is chosen', () => {
    expect(withViewAs({ 'X-Preview-User': 'mallory', a: 'b' }, null)).toEqual({ a: 'b' })
  })
})
