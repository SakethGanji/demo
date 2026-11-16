import { ResonanceWave } from '../components/ui'

export function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <div className="page">
      <h1 className="page-title">{title}</h1>
      <div className="card empty">
        <ResonanceWave width={160} height={28} color="var(--accent-primary)" />
        <h2 className="card-title">Nothing here yet</h2>
        <p className="helper" style={{ maxWidth: 420 }}>{body}</p>
      </div>
    </div>
  )
}
