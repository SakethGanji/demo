import { useEffect, useState } from 'react'
import { Icon } from '@/shared/primitives/Icon'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { useApi } from '@/shared/api/mode'
import { PatchCard, type Patch } from './components/PatchCard'

/** Stays mounted while closed so applied patches and your questions survive reopening it. */
export function VelaSidecar({ open, context, patches, onClose }: { open: boolean; context: string; patches: Patch[]; onClose: () => void }) {
  const { mode } = useApi()
  const [thinking, setThinking] = useState(true)
  const [applied, setApplied] = useState<Set<string>>(new Set())
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [q, setQ] = useState('')
  const [asked, setAsked] = useState<{ q: string; done: boolean }[]>([])

  useEffect(() => { setThinking(true); const t = setTimeout(() => setThinking(false), 1100); return () => clearTimeout(t) }, [context])

  const ask = () => {
    if (!q.trim()) return
    const idx = asked.length
    setAsked((a) => [...a, { q: q.trim(), done: false }])
    setQ('')
    setTimeout(() => setAsked((a) => a.map((x, i) => (i === idx ? { ...x, done: true } : x))), 1400)
  }

  return (
    <aside className="sidecar" aria-label="Vela AI Assistant" hidden={!open}>
      <div className="sidecar-head">
        <VelaCrest size={20} />
        <b className="card-title">Vela</b>
        <span className="micro-label" style={{ marginLeft: 4 }}>AI Assistant</span>
        <span className="spacer" />
        <button className="icon-btn" aria-label="Close Vela" onClick={onClose}><Icon.close width={16} height={16} /></button>
      </div>
      <div className="sidecar-body">
        <div className="vela-ctx"><span className="micro-label">Context</span><span className="chip">{context}</span><span className="pill warning" data-testid="vela-simulated">SIMULATED</span></div>
        {mode === 'live' && <p className="helper" style={{ fontSize: 12 }} data-testid="vela-note">Vela is a prototype: these findings are scripted, not computed from your runs, and their patches change the Training Gym prototype only.</p>}
        {thinking ? (
          <div className="thinking"><ResonanceWave width={84} height={18} fast /> Reading this page</div>
        ) : (
          patches.filter((p) => !dismissed.has(p.id)).map((p) => (
            <PatchCard key={p.id} p={p} applied={p.applied ?? applied.has(p.id)}
              onApply={() => { p.apply?.(); setApplied((s) => new Set(s).add(p.id)) }}
              onDismiss={() => setDismissed((s) => new Set(s).add(p.id))} />
          ))
        )}
        {asked.map((a, i) => (
          <div key={i} className="stack" style={{ gap: 8 }}>
            <div className="chip" style={{ alignSelf: 'flex-end', fontFamily: 'var(--font-sans)', height: 'auto', padding: '4px 8px', whiteSpace: 'normal' }}>{a.q}</div>
            {a.done ? (
              <div className="patch">
                <div className="ph"><VelaCrest size={16} /><b className="card-title" style={{ fontSize: 13 }}>Finding</b></div>
                <p>Your 5-fold spread is <b>± 0.006</b> on macro F1, so differences under <b>0.012</b> between runs are within noise. Compare <span className="mono">run_894a</span> and <span className="mono">run_8a10</span> with a paired test in Experiments?</p>
              </div>
            ) : (
              <div className="thinking"><ResonanceWave width={84} height={18} fast /> Thinking</div>
            )}
          </div>
        ))}
      </div>
      <div className="sidecar-input">
        <div className="box">
          <input placeholder="Ask Vela about this run…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()} />
          <button className="icon-btn" aria-label="Send" onClick={ask} style={{ color: 'var(--accent-primary)' }}><Icon.send width={16} height={16} /></button>
        </div>
      </div>
    </aside>
  )
}
