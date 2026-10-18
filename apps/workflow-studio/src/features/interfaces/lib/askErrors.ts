import { ApiError, errorMessage } from '@/shared/api/http'

/** One sentence for each failure the contract names. */
export function askErrorText(e: unknown): { text: string; reasons: string[] } {
  if (e instanceof ApiError) {
    if (e.status === 503 || e.code === 'LLM_NOT_CONFIGURED') {
      return { text: "AI isn't set up on this server (no model key), so screen edits are off. The screens themselves still work.", reasons: [] }
    }
    if (e.code === 'AI_SPEC_INVALID') {
      // the engine puts the reasons under `detail.fields.reasons` (interface_ai.py); read the flat form too
      const d = e.details as { reasons?: unknown; fields?: { reasons?: unknown } }
      const r = d.fields?.reasons ?? d.reasons
      return {
        text: "The change didn't pass the checks, so nothing was applied. Try describing it differently.",
        reasons: Array.isArray(r) ? r.filter((x): x is string => typeof x === 'string').slice(0, 5) : [],
      }
    }
    if (e.status === 502 || e.code === 'AI_FAILED') {
      return { text: "The AI model couldn't answer just now, so nothing was applied. Try again in a moment.", reasons: [] }
    }
  }
  return { text: errorMessage(e), reasons: [] }
}
