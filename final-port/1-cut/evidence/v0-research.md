# How v0 generates apps (and bolt / Lovable / Artifacts / OSS clones)

Confidence note: v0's prompts are third-party leaks (unverified snapshots); Vercel blogs are primary. Gaps are marked UNKNOWN.

## Sources
- S1 https://vercel.com/blog/v0-composite-model-family (primary)
- S2 https://vercel.com/blog/how-we-made-v0-an-effective-coding-agent (primary)
- S3 https://leaked-system-prompts.com/prompts/v0/v0_20250306 (leaked prompt, Mar 2025, CodeProject era)
- S4 https://raw.githubusercontent.com/x1xhlol/system-prompts-and-models-of-ai-tools/main/v0%20Prompts%20and%20Tools/Prompt.txt (leaked, newer agentic v0)
- S5 https://dev.to/yuyz0112/how-i-reverse-engineered-vercels-v0dev-prompt-and-code-optimization-logic-2cli (2023-24 inference, not confirmed by Vercel)
- S6 https://v0.app/docs/faqs (primary)
- S7 https://raw.githubusercontent.com/stackblitz/bolt.new/main/app/lib/.server/llm/prompts.ts (primary source code)
- S8 https://raw.githubusercontent.com/stackblitz/bolt.new/main/app/routes/api.chat.ts (primary source code)
- S9 https://github.com/Nutlope/llamacoder and its architecture.md https://raw.githubusercontent.com/Nutlope/llamacoder/main/architecture.md
- S10 https://github.com/e2b-dev/fragments and https://raw.githubusercontent.com/e2b-dev/fragments/main/lib/prompt.ts
- S11 https://github.com/firecrawl/open-lovable
- S12 https://support.bolt.new/troubleshooting/preview-issues ; https://www.replay.io/blog/nut-new (anecdote)
- S13 Lovable: no leaked prompt found (search); only secondary "fixed stack React+TS+Tailwind+Supabase" https://www.lowcode.agency/blog/how-does-lovable-work
- S14 Artifacts (secondary): https://simonwillison.net/2024/Oct/23/claude-artifact-runner/

## 1. Pipeline per message
- v0 (hosted, Vercel's own description): pre-process (system prompt + recent messages, older summarized) -> RAG (docs, UI examples) -> ONE base-model generation (frontier model; "v0-1.5-md uses Sonnet 4") OR a small "Quick Edit" model for narrow edits -> AutoFix runs WHILE streaming (no extra user-visible call) -> final pass -> linter. [S1]
- So per user message: 1 big generation call; the rest are tiny/fast fixers or deterministic code. No separate planning call or requirements questions in the classic v0. The old prompt did have an in-response <Thinking> block before the CodeProject (same call, not a separate one). [S1, S3]
- Clarifying questions: classic v0 had none except "ask for env vars BEFORE outputting code" [S3]. New agentic v0 has an AskUserQuestions tool and EnterPlanMode only for larger projects; not on every message [S4].
- bolt.new: ONE streamText call per message, toolChoice 'none'; extra calls only to continue when the output hits max tokens (bounded by MAX_RESPONSE_SEGMENTS). No planning call, no questions. [S8]
- llamacoder / fragments: single LLM call generating the code (inferred; fragments' prompt just says "generate a fragment" with a template). [S9, S10]

## 2. Output format
- v0 (Mar 2025): one `<CodeProject id="...">` per response containing fenced blocks like ```tsx file="path" ... ```; kebab-case file names; same project id across turns; multi-file (utils, hooks, types, entry component) [S3]. Small changes use `<QuickEdit>`; "medium to large" changes: write the COMPLETE file again; only edit relevant files, not all [S3].
- Original v0 (2023): model emitted JSX body + import list + icon list; a post-processor extracted used components, generated shadcn imports by name, and built lucide icons. Author reports ~30% failures (wrong import paths, forgotten icon imports) before that post-process step [S5, inferred].
- bolt: one `<boltArtifact id title>` with ordered `<boltAction type="file|shell" filePath>`; "ALWAYS provide FULL updated content", placeholders like "// rest remains the same" forbidden; user edits sent back to model as diff-or-full-file, whichever is smaller; install deps first [S7].
- fragments: structured object ("fragment") with template choice + code; "Do not wrap code in backticks" [S10].
- Default first generation (v0): Next.js App Router, several components, not one giant page.tsx (new prompt: "Do not have one large page.tsx") [S3,S4]. Older Dec-2024 prompt said "include all components and hooks in ONE FILE" (secondary snippet from HF dataset https://huggingface.co/datasets/Nymbo/Official_LLM_System_Prompts/blob/e2a4cb7e933adc4d6828267a85f3ecb610385ca9/Vercel%20v0%209.12.2024.md).

## 3. Component library / styling
- v0: "Tailwind CSS, Next.js, shadcn/ui components, and Lucide React icons are pre-installed"; v0 does NOT output shadcn files unless modifying them; use CSS-variable colors (bg-primary); never raw <svg> icons, always lucide-react; placeholders via /placeholder.svg?height=&width= [S3]. New v0 tells the model what exists via a starter-template file listing + Read/Glob/Grep tools; components/ui/* "already exist and should be used directly" [S4].
- Charts: Recharts via shadcn chart pattern [S4].
- llamacoder: esbuild-wasm + esm.sh in sandboxed iframe, shadcn files injected into the preview FS, pinned versions via import map, "Try to fix" button sends build/runtime/import errors back to the LLM [S9].

## 4. Data / backend
- Classic v0 first generation: front-end only, mock data in code; no backend [S3 has no persistence guidance at all].
- Newest agentic v0: "MUST default to building real apps with proper backend storage integrations"; Supabase default DB/auth, Vercel Blob for files; NEVER localStorage unless asked; no ORM; SWR/RSC data, no fetch in useEffect [S4]. This is a full-stack Next.js agent in a VM sandbox, NOT the cheap one-shot path [S6].
- bolt: full-stack in WebContainer; prefers Vite, SQLite/libsql (no native code) [S7].
- Lovable: React+TS+Tailwind+Supabase by default (secondary) [S13].

## 5. Error handling
- Biggest lever in v0 is AUTOMATIC and INVISIBLE: LLM errors "as often as 10% of the time"; streaming rewriter (LLM Suspense) fixes things mid-stream (e.g. nonexistent lucide icon -> nearest real icon via embedding, <100 ms, no model call); post-stream autofixers (AST) add missing providers (QueryClientProvider), missing package.json deps, JSX/TS repairs, <250 ms; small fine-tuned autofix model; then linter [S1, S2]. Reported error-free rate: v0-1.5-md 93.9% vs Sonnet 4 raw 64.7% and Opus 4 78.4% [S1].
- User-initiated: "Fix with v0" button on errors sends logs to v0 (secondary mirror https://3patines.v0.build/docs/agentic-features); new v0 agent has error-fixing in its loop [search result summary].
- bolt: errors surface to user, who clicks "Ask Bolt to fix" / pastes console error; official support advice is don't re-run same prompt, paste the error [S12]. One user report of auto-retry giving up after 5 attempts (anecdote) [S12]. No retry loop in open-source api.chat.ts [S8].
- llamacoder: user-initiated "Try to fix" [S9].

## 6. Edits after first build
- v0: QuickEdit model for small changes; full rewrite of only the affected files for larger ones; project id kept stable; older messages summarized [S1,S3].
- bolt: always full file content (no "rest unchanged"), "edit the latest version", diffs only used on the INPUT side to tell the model what the user changed [S7].
- Protection against breakage in all: constrained stack, small files, tell model current file contents, not touching deps files (fragments) [S10].

## 7. Preview / runtime
- v0 originally in-browser; now Vercel Sandbox VM running real builds, replaced browser preview [S6].
- bolt: StackBlitz WebContainers (in-browser Node) [S7].
- llamacoder: esbuild-wasm + esm.sh import map in iframe (same approach as ours) [S9].
- fragments / open-lovable: E2B or Vercel sandboxes [S10, S11].
- Artifacts: single-file React in iframe with preloaded Tailwind/shadcn/lucide/recharts (secondary) [S14].

## 8. What makes them reliable (synthesis)
1. Fixed stack the model knows cold (Next/React, Tailwind, shadcn, lucide, Recharts); prompt tells it what is preinstalled and not to re-emit it.
2. One generation call; no multi-agent hand-offs to disagree with each other.
3. Deterministic post-processing of the stream (imports, icon names, missing deps/providers) rather than asking the model to fix its own trivial mistakes; Vercel quantifies double-digit success-rate gains [S2].
4. Small fast fixer path for edits; full-file output for anything bigger.
5. Errors fed back as concrete text; fix is cheap and scoped.
6. Front-end-first output with mock data; backends are a separate, later integration (except the newest agentic v0).
7. Format discipline: one container, path-annotated files, no placeholders, stable project id.

## Recommended minimal builder for us
- Calls: exactly 1 streaming generation call per "build" message. No plan step, no requirement questions (at most: if the prompt is truly empty, generate with sensible defaults and state assumptions in 1 line).
- Output format: single container with path-annotated file fences (v0 style: ```tsx file="App.tsx"), parsed by code; no JSON-wrapped code, no custom multi-tag grammar. Full file contents only, never "rest unchanged".
- Files: default to 1 file (App.tsx) up to ~3 (App.tsx + components + data); entry is always App.tsx exporting default. Fewer files = fewer import mismatches.
- Stack in prompt: React + Tailwind + our kit; list EVERY kit export with its props signature and a short usage example (like shadcn docs in S5); forbid anything not in the list (no other npm imports, no raw <svg>, lucide only from an allowlist).
- Data: front-end only first generation. Realistic mock data inline as consts plus useState; optional localStorage for persistence via a kit hook (`useLocalCollection`). No generated backend modules in the one-shot path; wire real data later as a second, explicit step.
- Deterministic post-processor (copy v0's LLM Suspense/autofixers): parse imports with esbuild/AST; auto-add missing kit imports by name; map unknown icons to nearest allowlisted icon; strip unknown imports with a clear stub; ensure default export; strip markdown fences.
- Validate before showing: run the esbuild-wasm bundle + a headless render smoke test in the iframe; collect build/runtime error text.
- Fix loop: automatic, max 2 retries, one call each, sending the failing file(s) + exact error text, requesting the full corrected file; if still failing show the last good version/the error and a user "Fix" button (bolt/v0/llamacoder all keep this user-initiated).
- Edits: small, scoped requests (text/colour/reorder) -> one call returning only the changed file(s) in full; keep prior files verbatim in the prompt; never regenerate the whole app unless asked. Optionally add a search/replace QuickEdit later; start with full-file replace of touched files.
- Keep the model's reasoning inside the same call (brief <thinking> or none); no planner call.
- Stream the output and render when the container closes; show file tabs while streaming.
- Model: one strong model for build; optionally a cheaper model for the fix retries.
- Metric to track (as Vercel does): % of first generations that render without error; add a 20-prompt regression set of bank-style requests.
