// LLM planner: OpenAI-compatible chat completions or Gemini generateContent, via env vars.
//   TB_LLM_PROVIDER = openai | gemini
//   TB_LLM_MODEL    = e.g. gpt-4o-mini | gemini-3.8-flash
//   TB_LLM_API_KEY  = key (never logged)
//   TB_LLM_BASE_URL = optional; default https://api.openai.com/v1 or https://generativelanguage.googleapis.com/v1beta
// The model only ever sees scrubbed input (see explore.ts) and must return ONE action as strict JSON.
import { validateAction, ACTION_NAMES, type Action } from '../actions.ts';
import type { Planner, PlannerInput } from './types.ts';

export interface LlmConfig { provider: 'openai' | 'gemini'; model: string; apiKey: string; baseUrl: string }

export function llmConfigFromEnv(env: NodeJS.ProcessEnv = process.env): LlmConfig {
  const provider = (env.TB_LLM_PROVIDER ?? '').toLowerCase();
  if (provider !== 'openai' && provider !== 'gemini') throw new Error('TB_LLM_PROVIDER must be "openai" or "gemini"');
  const apiKey = env.TB_LLM_API_KEY ?? '';
  if (!apiKey) throw new Error('TB_LLM_API_KEY is not set');
  const model = env.TB_LLM_MODEL ?? (provider === 'gemini' ? 'gemini-3.8-flash' : 'gpt-4o-mini');
  const baseUrl = (env.TB_LLM_BASE_URL ?? (provider === 'gemini'
    ? 'https://generativelanguage.googleapis.com/v1beta'
    : 'https://api.openai.com/v1')).replace(/\/$/, '');
  return { provider, model, apiKey, baseUrl };
}

export const SYSTEM_PROMPT = `You are a QA test author driving a web browser one action at a time to accomplish a test goal.
Reply with EXACTLY ONE JSON object and nothing else. Schema:
{"action": one of ${JSON.stringify(ACTION_NAMES)},
 "target": {"role": "<aria role>", "name": "<accessible name>"} | {"label": "<form label>"} | {"text": "<visible text>"},   (optional "within": another target to scope to)
 "url": "<path>" (goto only), "value": "<text>" (fill/otp only), "option": "<label>" (select only), "key": "<key>" (press only),
 "file": "<fixture path>" (upload only), "state": "visible"|"hidden" (waitFor only), "contains": ["<substring>"] (download only, optional),
 "text": "<substring>" (expectText only), "count": <int> (expectCount only),
 "group": "<short human step title, e.g. 'Log in'>",
 "reason": "<why>" (done only), "success": true|false (done only)}
Action notes:
- "otp": enters a one-time code into the group of single-character code fields. Use value "{{totp:<key>}}" for a TOTP credential key.
- "upload": sets a file input (by its label) to a fixture path. "download": clicks a link/button that downloads a file; add "contains" to assert the file's text.
- "hover": reveals hover-only menus. "waitFor": waits for an element to become visible or hidden (use after an async action before asserting).
Rules:
- Use roles and accessible names exactly as they appear in the accessibility snapshot.
- For credentials, write the placeholder (e.g. "{{password}}") as the fill value; for a TOTP code use "otp" with "{{totp:<key>}}". You never see real values.
- After acting, add expect* actions that prove the goal (visible element, text, row counts). Then return done.
- If a step is blocked by policy, do NOT retry it or look for a workaround; continue the goal without it or return done.
- Never click anything that moves money, deletes data or approves things unless the goal is explicitly about checking that it is blocked.`;

export function buildUserPrompt(input: PlannerInput): string {
  const hist = input.history.slice(-20).map((h) =>
    `${h.step}. ${JSON.stringify(h.action)} -> ${h.outcome}${h.message ? ` (${h.message})` : ''}`).join('\n');
  const snap = input.snapshot.length > 12000 ? input.snapshot.slice(0, 12000) + '\n... (truncated)' : input.snapshot;
  return [
    `GOAL: ${input.goal}`,
    `CURRENT URL: ${input.url}`,
    `CREDENTIAL PLACEHOLDERS AVAILABLE: ${input.credentialKeys.map((k) => `{{${k}}}`).join(', ') || '(none)'}`,
    input.lastError ? `LAST STEP ERROR: ${input.lastError}` : '',
    `HISTORY:\n${hist || '(none)'}`,
    `ACCESSIBILITY SNAPSHOT:\n${snap}`,
    'Next action JSON:',
  ].filter(Boolean).join('\n\n');
}

/** Pull the first JSON object out of a model reply (tolerates ```json fences, nothing else). */
export function parseModelJson(text: string): unknown {
  const t = text.trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '');
  return JSON.parse(t);
}

export class LlmPlanner implements Planner {
  readonly name: string;
  private readonly cfg: LlmConfig;
  private readonly fetchImpl: typeof fetch;
  constructor(cfg: LlmConfig, fetchImpl: typeof fetch = fetch) {
    this.cfg = cfg;
    this.fetchImpl = fetchImpl;
    this.name = `llm:${cfg.provider}:${cfg.model}`;
  }

  private async complete(user: string): Promise<string> {
    const { cfg } = this;
    if (cfg.provider === 'openai') {
      const r = await this.fetchImpl(`${cfg.baseUrl}/chat/completions`, {
        method: 'POST',
        signal: AbortSignal.timeout(60_000),
        headers: { 'content-type': 'application/json', authorization: `Bearer ${cfg.apiKey}` },
        body: JSON.stringify({
          model: cfg.model,
          temperature: 0,
          response_format: { type: 'json_object' },
          messages: [{ role: 'system', content: SYSTEM_PROMPT }, { role: 'user', content: user }],
        }),
      });
      const body = await r.text();
      if (!r.ok) throw new Error(`LLM HTTP ${r.status}: ${body.slice(0, 400)}`);
      const j = JSON.parse(body) as { choices?: { message?: { content?: string } }[] };
      return j.choices?.[0]?.message?.content ?? '';
    }
    const r = await this.fetchImpl(`${cfg.baseUrl}/models/${encodeURIComponent(cfg.model)}:generateContent`, {
      method: 'POST',
      signal: AbortSignal.timeout(60_000),
      headers: { 'content-type': 'application/json', 'x-goog-api-key': cfg.apiKey },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
        contents: [{ role: 'user', parts: [{ text: user }] }],
        generationConfig: { temperature: 0, responseMimeType: 'application/json' },
      }),
    });
    const body = await r.text();
    if (!r.ok) throw new Error(`LLM HTTP ${r.status}: ${body.slice(0, 400)}`);
    const j = JSON.parse(body) as { candidates?: { content?: { parts?: { text?: string }[] } }[] };
    return j.candidates?.[0]?.content?.parts?.map((p) => p.text ?? '').join('') ?? '';
  }

  async next(input: PlannerInput): Promise<Action> {
    const user = buildUserPrompt(input);
    let lastErr = '';
    for (let attempt = 0; attempt < 2; attempt++) {
      const text = await this.complete(attempt ? `${user}\n\nYour previous reply was invalid: ${lastErr}. Reply with one valid JSON action.` : user);
      try {
        return validateAction(parseModelJson(text));
      } catch (e) {
        lastErr = (e as Error).message;
      }
    }
    throw new Error(`model returned invalid action twice: ${lastErr}`);
  }
}
