import { useState } from 'react'
import { api } from '@/shared/api/client'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { DECIDER } from '@/features/vela/components/DeciderTrail'
import { useFetch } from '@/shared/hooks/useFetch'

const ORDER = ['anthropic-sdk', 'claude-cli', 'computed'] as const
const fmt = (s: number | null) => (s == null ? '—' : s < 0.01 ? `${(s * 1000).toFixed(2)} ms` : `${s.toFixed(2)} s`)

/** F5: the language-model router behind Vela and Autopilot: its route, latency per decider, cache and timeouts. Every
 *  number comes from GET /api/llm; the timeouts are saved on the server (PUT /api/llm/settings). */
export function LlmCard() {
  const { data: st, setData: setSt, err, setErr, reload: load } = useFetch(() => api.llm.status(), [], { pollMs: 5000 })
  const [saved, setSaved] = useState<string | null>(null)
  const put = (patch: Parameters<typeof api.llm.update>[0], what: string) =>
    api.llm.update(patch).then((s) => { setSt(s); setSaved(what); setTimeout(() => setSaved(null), 2500) }).catch((e) => setErr((e as Error).message))

  if (err && !st) return <div className="card"><div className="card-body"><p className="helper" role="alert">✕ Language model status: {err}</p></div></div>
  if (!st) return null
  const s = st.settings
  const hits = st.stats.cache_hits, calls = st.stats.calls
  const avail: Record<string, { ok: boolean; why: string }> = {
    'anthropic-sdk': { ok: st.route.includes('anthropic-sdk'), why: !s.use_sdk ? 'off below' : st.sdk.why + (st.sdk.base_url ? ` · gateway ${st.sdk.base_url}` : '') },
    'claude-cli': { ok: st.route.includes('claude-cli'), why: !s.use_cli ? 'off below' : st.cli.path ?? 'not on PATH' },
    computed: { ok: true, why: 'always: the numbers and findings the workbench computed' },
  }
  return (
    <div className="card" data-testid="llm-card" style={{ gridColumn: '1 / -1' }}>
      <div className="card-head">
        <div className="left"><h2 className="card-title">Language Model</h2><span className="helper" style={{ fontSize: 12 }}>Vela's answers and wording, Autopilot's decisions · the numbers never come from it</span></div>
        <div className="row">{s.enabled ? <StatusPill status="success">On · {s.model}</StatusPill> : <StatusPill status="warning">Off · computed text only</StatusPill>}{saved && <span className="micro-label">saved {saved}</span>}</div>
      </div>
      <div className="card-body">
        <div className="table-wrap"><table className="table" data-testid="llm-route">
          <thead><tr><th>#</th><th>Decider</th><th>Available</th><th>Timeout</th><th className="num">n</th><th className="num">t̃ (p50)</th><th className="num">τ₉₅ (p95)</th><th className="num">last</th></tr></thead>
          <tbody>
            {ORDER.map((k, i) => {
              const l = st.latency[k]
              return (
                <tr key={k} data-testid={`llm-row-${k}`}>
                  <td className="mono">{i + 1}</td>
                  <td><b>{DECIDER[k]}</b><div className="muted" style={{ fontSize: 11.5, whiteSpace: 'normal', maxWidth: 360 }}>{avail[k].why}</div></td>
                  <td>{avail[k].ok ? <StatusPill status="success">yes</StatusPill> : <StatusPill status="neutral">no</StatusPill>}</td>
                  <td className="mono">{k === 'anthropic-sdk' ? `T_sdk = ${s.sdk_timeout} s` : k === 'claude-cli' ? `T_cli = ${s.cli_timeout} s` : '—'}</td>
                  <td className="num mono">{l?.n ?? 0}</td><td className="num mono">{fmt(l?.p50_s ?? null)}</td><td className="num mono">{fmt(l?.p95_s ?? null)}</td><td className="num mono">{fmt(l?.last_s ?? null)}</td>
                </tr>
              )
            })}
          </tbody>
        </table></div>
        <p className="helper" style={{ fontSize: 12, marginTop: 10 }}>
          A call tries the deciders in order and stops at the first answer. Each path gets <span className="math">min(T_path, T − elapsed)</span> seconds,
          where <span className="math">T</span> = {s.total_timeout} s is the total budget of one call; a path that times out or fails hands over to the next, and the last
          is always the computed text. <span className="math">n</span> is the number of calls that decider ended, <span className="math">t̃</span> and <span className="math">τ₉₅</span> the
          50th and 95th percentiles of their model time over the last {Math.max(...ORDER.map((k) => st.latency[k]?.n ?? 0), 0)} calls (up to 200 kept), since the API started.
        </p>
        <div className="row" style={{ gap: 16, alignItems: 'baseline', flexWrap: 'wrap', marginTop: 6 }} data-testid="llm-cache">
          <span><span className="micro-label">Cache</span> key = (h(prompt), h(facts), model), h = SHA-256 · <span className="math">h_rate</span> = hits ÷ calls = {hits} ÷ {calls} = <b className="mono">{calls ? (hits / calls).toFixed(2) : '—'}</b> · {st.stats.cache_size} answers kept for {s.cache_ttl} s · {st.stats.shared_in_flight} asks shared an identical call in flight</span>
          <button className="btn sm" onClick={() => api.llm.clearCache().then(load)} data-testid="llm-clear-cache">Clear Cache</button>
        </div>
        <details className="an-adv" style={{ marginTop: 12 }}><summary className="micro-label">Advanced · timeouts and paths</summary>
          <div className="row" style={{ gap: 18, flexWrap: 'wrap', marginTop: 10, alignItems: 'center' }}>
            <label className="row" style={{ gap: 6 }}><span className="math">T_sdk</span><NumberInput label="SDK timeout (s)" value={s.sdk_timeout} min={0.5} max={600} step={0.5} onChange={(v) => put({ sdk_timeout: v }, 'T_sdk')} /><span className="micro-label">s</span></label>
            <label className="row" style={{ gap: 6 }}><span className="math">T_cli</span><NumberInput label="CLI timeout (s)" value={s.cli_timeout} min={0.5} max={600} step={0.5} onChange={(v) => put({ cli_timeout: v }, 'T_cli')} /><span className="micro-label">s</span></label>
            <label className="row" style={{ gap: 6 }}><span className="math">T</span><NumberInput label="Total timeout (s)" value={s.total_timeout} min={0.5} max={600} step={0.5} onChange={(v) => put({ total_timeout: v }, 'T')} /><span className="micro-label">s total</span></label>
            <label className="row" style={{ gap: 6 }}>TTL<NumberInput label="Cache TTL (s)" value={s.cache_ttl} min={0} max={86400} step={60} onChange={(v) => put({ cache_ttl: v }, 'TTL')} /><span className="micro-label">s</span></label>
          </div>
          <div className="row" style={{ gap: 18, marginTop: 10 }}>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={s.enabled} onChange={(e) => put({ enabled: e.target.checked }, 'model on/off')} /> Use a language model</label>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={s.use_sdk} onChange={(e) => put({ use_sdk: e.target.checked }, 'SDK path')} /> Anthropic SDK</label>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={s.use_cli} onChange={(e) => put({ use_cli: e.target.checked }, 'CLI path')} /> Claude CLI</label>
            <button className="btn sm" onClick={() => put({ sdk_timeout: null, cli_timeout: null, total_timeout: null, cache_ttl: null, enabled: null, use_sdk: null, use_cli: null }, 'defaults')}>Restore Defaults</button>
          </div>
          <p className="helper" style={{ fontSize: 12, marginTop: 8 }}>Saved on the API (data/llm_settings.json) for every user. The SDK path needs ANTHROPIC_API_KEY in the API's environment; the key is never shown or stored here.</p>
        </details>
      </div>
    </div>
  )
}
