import { useEffect, useState } from 'react'
import { Motion } from './motion/Motion'
import { NteractProvider } from './nteract/NteractProvider'
import { NteractGallery } from './nteract/NteractGallery'

const useHash = () => {
  const [h, setH] = useState(() => location.hash.slice(1) || '/')
  useEffect(() => { const f = () => setH(location.hash.slice(1) || '/'); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  return h
}

/** Two pages: #/motion (the animated scenes) and #/nteract (every nteract-elements component). */
export function App() {
  const route = useHash()
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try { const t = localStorage.getItem('ne-theme'); if (t === 'light' || t === 'dark') return t } catch { /* storage blocked */ }
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('ne-theme', theme) } catch { /* storage blocked */ }
  }, [theme])

  const [, section, id] = route.split('/')
  const page = section === 'nteract' ? 'nteract' : 'motion'
  return (
    <div className="ne-root app">
      <header className="app__top">
        <nav className="app__nav">
          <a href="#/motion" className={page === 'motion' ? 'on' : ''}>Motion</a>
          <a href="#/nteract" className={page === 'nteract' ? 'on' : ''}>nteract</a>
        </nav>
        <button className="app__theme" aria-label="Toggle theme" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}>
          {theme === 'dark' ? 'Light' : 'Dark'}
        </button>
      </header>
      <NteractProvider>{page === 'nteract' ? <NteractGallery id={id} /> : <Motion />}</NteractProvider>
    </div>
  )
}
