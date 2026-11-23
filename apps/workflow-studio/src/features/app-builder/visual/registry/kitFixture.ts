/** Tests only: the kit sources as an app carries them (`kit/<file>`), read straight from the engine's app_kit. */
const RAW = import.meta.glob('../../../../../../workflow-engine/src/services/app_kit/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

export const KIT_FILES: Record<string, string> = Object.fromEntries(
  Object.entries(RAW).map(([p, src]) => [`kit/${p.slice(p.lastIndexOf('/') + 1)}`, src]),
)
