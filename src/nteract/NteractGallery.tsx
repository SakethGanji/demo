/**
 * The nteract-elements gallery (#/nteract): every registry item, component file and widget control,
 * rendered live, with a computed coverage counter. Widgets are opened by a pretend kernel through the
 * widget store's comm router, exactly as a real kernel would.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { WidgetStoreProvider, useWidgetStoreRequired } from '@/components/widgets/widget-store-context'
import { ALL_FILES, CONTROL_FILES, REGISTRY_ITEMS, WIDGET_FILES, coverageVersion, markRendered, registeredModels, renderedModels, subscribeCoverage } from './coverage'
import { createKernel, type Kernel } from './kernel'
import { SECTIONS, setGalleryKernel } from './sections'
import './gallery.css'

const CATEGORIES = [...new Set(SECTIONS.map(s => s.category))]

/** Opens every widget on the kernel side, then reports nested widgets (children of containers) by scanning the DOM. */
function KernelBridge({ kernel, root }: { kernel: Kernel; root: React.RefObject<HTMLDivElement | null> }) {
  const { handleMessage, store } = useWidgetStoreRequired()
  useEffect(() => { kernel.attach(handleMessage, id => !!store.getModel(id)) }, [kernel, handleMessage, store])
  useEffect(() => {
    const el = root.current
    if (!el) return
    let raf = 0
    const scan = () => {
      raf = 0
      const names: string[] = []
      el.querySelectorAll<HTMLElement>('[data-widget-id]').forEach(n => { const m = store.getModel(n.dataset.widgetId!); if (m) names.push(m.modelName) })
      markRendered(names)
    }
    const mo = new MutationObserver(() => { if (!raf) raf = requestAnimationFrame(scan) })
    mo.observe(el, { childList: true, subtree: true })
    scan()
    return () => { mo.disconnect(); if (raf) cancelAnimationFrame(raf) }
  }, [root, store])
  return null
}

function useCoverage() {
  useSyncExternalStore(subscribeCoverage, coverageVersion)
  const rendered = renderedModels()
  const models = registeredModels()
  const regCovered = new Set(SECTIONS.flatMap(s => s.registry))
  regCovered.add('all') // the gallery itself is the `all` bundle
  const declaredFiles = new Set(SECTIONS.flatMap(s => s.files))
  const fileCovered = (f: string) => declaredFiles.has(f) || (WIDGET_FILES[f] ? WIDGET_FILES[f].some(m => rendered.has(m)) : false)
  const files = ALL_FILES.filter(f => !f.endsWith('.d.ts'))
  return {
    registry: { done: REGISTRY_ITEMS.filter(r => regCovered.has(r)).length, total: REGISTRY_ITEMS.length, missing: REGISTRY_ITEMS.filter(r => !regCovered.has(r)) },
    models: { done: models.filter(m => rendered.has(m)).length, total: models.length, missing: models.filter(m => !rendered.has(m)) },
    controls: { done: CONTROL_FILES.filter(fileCovered).length, total: CONTROL_FILES.length, missing: CONTROL_FILES.filter(f => !fileCovered(f)) },
    files: { done: files.filter(fileCovered).length, total: files.length, missing: files.filter(f => !fileCovered(f)) },
  }
}

function Coverage() {
  const c = useCoverage()
  const stat = (label: string, s: { done: number; total: number }) => (
    <span className={`gx-stat${s.done === s.total ? ' gx-stat--ok' : ''}`}><b>{s.done}/{s.total}</b> {label}</span>
  )
  const missing = [
    ...c.registry.missing.map(m => `registry: ${m}`),
    ...c.controls.missing.map(m => `control file: ${m}`),
    ...c.models.missing.map(m => `widget model: ${m}`),
    ...c.files.missing.map(m => `file: ${m}`),
  ]
  return (
    <div className="gx-coverage" data-testid="coverage">
      <div className="gx-coverage__stats">
        {stat('registry items', c.registry)}<span className="gx-dot">·</span>
        {stat('widget controls', c.controls)}<span className="gx-dot">·</span>
        {stat('widget models', c.models)}<span className="gx-dot">·</span>
        {stat('component files', c.files)}
      </div>
      {missing.length > 0 && (
        <div className="gx-missing" data-testid="coverage-missing">
          <b>Not rendered yet ({missing.length}):</b> {missing.map(m => <code key={m}>{m}</code>)}
          <span className="gx-muted"> Widget models are counted as they mount — the list empties once every section has rendered.</span>
        </div>
      )}
    </div>
  )
}

export function NteractGallery({ id }: { id?: string }) {
  const kernel = useMemo(() => createKernel(), [])
  const root = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState(id ?? SECTIONS[0].id)
  setGalleryKernel(kernel)

  // App scrolls to the top on every route change in a parent effect; jump to the section after it.
  useEffect(() => {
    if (!id) return
    const t = setTimeout(() => document.getElementById(`gx-${id}`)?.scrollIntoView({ block: 'start' }), 30)
    return () => clearTimeout(t)
  }, [id])

  // highlight the section in view
  useEffect(() => {
    const els = SECTIONS.map(s => document.getElementById(`gx-${s.id}`)).filter(Boolean) as HTMLElement[]
    const io = new IntersectionObserver(entries => {
      const vis = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (vis[0]) setActive(vis[0].target.id.slice(3))
    }, { rootMargin: '-70px 0px -65% 0px' })
    els.forEach(e => io.observe(e))
    return () => io.disconnect()
  }, [])

  return (
    <WidgetStoreProvider sendMessage={kernel.receive}>
      <KernelBridge kernel={kernel} root={root} />
      <div className="docs gx-docs ne-root" ref={root}>
        <nav className="docs__nav" aria-label="nteract components">
          <a href="#/nteract" className={!id ? 'on' : ''}>Overview</a>
          {CATEGORIES.map(c => (
            <div key={c} className="docs__group">
              <div className="docs__cat">{c}</div>
              {SECTIONS.filter(s => s.category === c).map(s => <a key={s.id} href={`#/nteract/${s.id}`} className={s.id === active ? 'on' : ''}>{s.title}</a>)}
            </div>
          ))}
        </nav>
        <main className="docs__main gx-main">
          <header className="gx-hero">
            <div className="docs__kicker">nteract elements · live gallery</div>
            <h1>Every nteract component, running</h1>
            <p className="docs__lead">All registry items, every file under <code>src/components</code>, and every Jupyter widget control — opened by <code>comm_open</code> messages from a pretend kernel and rendered through <code>WidgetView</code>.</p>
            <Coverage />
            <pre className="gx-import"><code>npx shadcn@latest add @nteract/all</code></pre>
          </header>
          {CATEGORIES.map(c => (
            <div key={c} className="gx-cat">
              <h2 className="gx-cat__h">{c}</h2>
              {SECTIONS.filter(s => s.category === c).map(s => (
                <section key={s.id} id={`gx-${s.id}`} className="gx-section" data-section={s.id}>
                  <div className="gx-section__head">
                    <h3><a href={`#/nteract/${s.id}`}>{s.title}</a></h3>
                    <p>{s.desc}</p>
                  </div>
                  <div className="gx-preview" data-testid="preview"><s.Example /></div>
                  <pre className="gx-import"><code>{s.imports}</code></pre>
                </section>
              ))}
            </div>
          ))}
        </main>
      </div>
    </WidgetStoreProvider>
  )
}
