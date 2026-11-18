import type { LlmAttempt } from '@/shared/api/types'

/** F5: names of the language-model router's deciders (app/llm.py). */
export const DECIDER: Record<string, string> = {
  'anthropic-sdk': 'Anthropic SDK', 'claude-cli': 'Claude CLI', 'anthropic-api': 'Anthropic API', computed: 'computed text',
}

const sec = (s: number) => (s < 0.01 ? `${(s * 1000).toFixed(s < 0.001 ? 2 : 1)} ms` : `${s.toFixed(s < 10 ? 2 : 1)} s`)

/** The route one call took: each path in order with its outcome and time, ending in the decider. */
export function DeciderTrail({ attempts, source, seconds, cached, originalSeconds }: {
  attempts: LlmAttempt[]; source: string; seconds: number; cached?: boolean; originalSeconds?: number | null
}) {
  return (
    <span className="decider-trail" data-testid="decider-trail">
      {cached ? <span className="dt-step ok">cache hit · {sec(seconds)}{originalSeconds != null ? <span className="muted"> (first asked: {sec(originalSeconds)})</span> : null}</span> : null}
      {!cached && attempts.map((a, i) => (
        <span key={i} className={`dt-step ${a.ok ? 'ok' : a.skipped ? 'skip' : 'fail'}`} title={a.error ?? ''}>
          {DECIDER[a.source] ?? a.source}{' '}
          {a.ok ? `✓ ${sec(a.seconds)}` : a.skipped ? `– ${a.error}` : a.timed_out ? `✗ timed out at ${a.timeout_s ?? '?'} s` : `✗ ${(a.error ?? 'failed').slice(0, 60)}`}
          {i < attempts.length - 1 ? <span className="muted"> →</span> : null}
        </span>
      ))}
      {!cached && source === 'computed' ? <span className="dt-step fail"><span className="muted">→</span> computed text · {sec(seconds)}</span> : null}
    </span>
  )
}
