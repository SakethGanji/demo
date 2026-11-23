// LLM config, the same way test-builder takes it (TB_LLM_* env). If TB_LLM_* is not set, fall back to a Gemini
// key named GEMINI_API_KEY in the environment or in apps/workflow-engine/.env.local. The key is never logged.
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { llmConfigFromEnv, parseModelJson, type LlmConfig } from '../../test-builder/src/planners/llm.ts';
import { ROOT } from './store.ts';

function readEnvFileVar(file: string, name: string): string | undefined {
  if (!existsSync(file)) return undefined;
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const m = new RegExp(`^\\s*(?:export\\s+)?${name}\\s*=\\s*(.*)\\s*$`).exec(line);
    if (m) return m[1]!.replace(/^(['"])(.*)\1$/, '$2').trim() || undefined;
  }
  return undefined;
}

export function llmConfig(): LlmConfig | null {
  if (process.env.JOBS_LLM === 'off') return null;
  if (process.env.TB_LLM_PROVIDER) {
    try { return llmConfigFromEnv(); } catch { return null; }
  }
  const key = process.env.GEMINI_API_KEY ?? readEnvFileVar(join(ROOT, '..', 'workflow-engine', '.env.local'), 'GEMINI_API_KEY');
  if (!key) return null;
  return llmConfigFromEnv({ TB_LLM_PROVIDER: 'gemini', TB_LLM_MODEL: process.env.TB_LLM_MODEL ?? 'gemini-3.8-flash', TB_LLM_API_KEY: key });
}

/** One strict-JSON completion (Gemini generateContent or OpenAI-compatible), mirroring test-builder's LlmPlanner. */
export async function completeJson(cfg: LlmConfig, system: string, user: string): Promise<unknown> {
  let text: string;
  if (cfg.provider === 'openai') {
    const r = await fetch(`${cfg.baseUrl}/chat/completions`, {
      method: 'POST', signal: AbortSignal.timeout(60_000),
      headers: { 'content-type': 'application/json', authorization: `Bearer ${cfg.apiKey}` },
      body: JSON.stringify({ model: cfg.model, temperature: 0, response_format: { type: 'json_object' }, messages: [{ role: 'system', content: system }, { role: 'user', content: user }] }),
    });
    const body = await r.text();
    if (!r.ok) throw new Error(`LLM HTTP ${r.status}`);
    text = (JSON.parse(body) as { choices?: { message?: { content?: string } }[] }).choices?.[0]?.message?.content ?? '';
  } else {
    const r = await fetch(`${cfg.baseUrl}/models/${encodeURIComponent(cfg.model)}:generateContent`, {
      method: 'POST', signal: AbortSignal.timeout(60_000),
      headers: { 'content-type': 'application/json', 'x-goog-api-key': cfg.apiKey },
      body: JSON.stringify({ systemInstruction: { parts: [{ text: system }] }, contents: [{ role: 'user', parts: [{ text: user }] }], generationConfig: { temperature: 0, responseMimeType: 'application/json' } }),
    });
    const body = await r.text();
    if (!r.ok) throw new Error(`LLM HTTP ${r.status}`);
    text = (JSON.parse(body) as { candidates?: { content?: { parts?: { text?: string }[] } }[] }).candidates?.[0]?.content?.parts?.map((p) => p.text ?? '').join('') ?? '';
  }
  return parseModelJson(text);
}
