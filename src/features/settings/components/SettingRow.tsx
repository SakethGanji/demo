import { usePrefs, type Prefs } from '@/shared/lib/prefs'
import { Segmented } from '@/shared/primitives/Segmented'

export function SettingRow<K extends keyof Prefs>({ k, name, help, options }: { k: K; name: string; help: string; options: Prefs[K][] }) {
  const { prefs, set } = usePrefs()
  return (
    <div className="set-row">
      <div><b>{name}</b><p className="helper">{help}</p></div>
      <Segmented options={options as string[]} value={prefs[k] as string} onChange={(v) => set({ [k]: v } as Partial<Prefs>)} />
    </div>
  )
}
