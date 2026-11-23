import wordmark from '@/shared/assets/ensemble-wordmark-on-shell.svg'
import { StringField } from '@/shared/components/StringField'
import { Icon } from '@/shared/primitives/Icon'
import { useApi } from '@/shared/api/mode'
import { FirstRunLive, LiveHeroRead, LiveHome } from './LiveHome'

export function HomePage({ go, openPalette }: { go: (r: string) => void; openPalette: () => void }) {
  const { status } = useApi()

  return (
    <div className="page">
      <section className="hero">
        <div className="hero-top">
          <div className="hero-lockup">
            <span className="hero-eyebrow">PROJECT</span>
            <img src={wordmark} alt="ENSEMBLE" height={36} />
          </div>
          <LiveHeroRead />
        </div>
        <StringField height={104} />
        <div className="hero-bottom">
          <div>
            <h1 className="hero-title">Many models, one framework.</h1>
            <p className="hero-sub">A toolkit for training, comparing and shipping models. Upload a table and Build shows what you can make from it.</p>
          </div>
          <div className="row" style={{ gap: 12 }}>
          <button className="btn primary" style={{ height: 40, padding: '0 16px' }} onClick={() => go('build')}>Build →</button>
          <button className="hero-cmd" onClick={openPalette}>
            <Icon.search width={16} height={16} /><span>Find a page, run or model</span><kbd>⌘K</kbd>
          </button>
          </div>
        </div>
      </section>

      {status && status.runs === 0 && <FirstRunLive go={go} />}

      <LiveHome go={go} />
    </div>
  )
}
