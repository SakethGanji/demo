/**
 * The project's supporting pages ("System"): the dock on the home screen and the System group of the jump menu.
 * Each has its own icon and hue, like an app on a home screen. `overlay` places open as a sheet over the current
 * page instead of navigating (Settings).
 */
import { CheckCheck, CodeXml, Database, FileCode2, FileSpreadsheet, History, KeyRound, Plug, Settings2, Waypoints, type LucideIcon } from 'lucide-react'

export type PlaceKey = 'runs' | 'approvals' | 'data' | 'backend' | 'code' | 'secrets' | 'knowledge' | 'datasets' | 'apis' | 'settings'

export interface Place {
  key: PlaceKey
  label: string
  Icon: LucideIcon
  /** icon colour and tile tint */
  text: string
  tint: string
  to?: string
  overlay?: Record<string, unknown>
}

export const PLACES: Place[] = [
  { key: 'runs', label: 'Runs', Icon: History, text: 'text-hue-runs', tint: 'bg-hue-runs/16', to: '/p/$project/runs' },
  { key: 'approvals', label: 'Approvals', Icon: CheckCheck, text: 'text-hue-flow', tint: 'bg-hue-flow/16', to: '/p/$project/approvals' },
  { key: 'data', label: 'Data', Icon: Database, text: 'text-hue-data', tint: 'bg-hue-data/16', to: '/p/$project/data' },
  { key: 'backend', label: 'Backend', Icon: FileCode2, text: 'text-hue-backend', tint: 'bg-hue-backend/16', to: '/p/$project/backend' },
  { key: 'code', label: 'Code', Icon: CodeXml, text: 'text-hue-backend', tint: 'bg-hue-backend/16', to: '/p/$project/code' },
  { key: 'secrets', label: 'Secrets', Icon: KeyRound, text: 'text-hue-secrets', tint: 'bg-hue-secrets/16', to: '/p/$project/secrets' },
  { key: 'knowledge', label: 'Knowledge', Icon: Waypoints, text: 'text-hue-ai', tint: 'bg-hue-ai/16', to: '/p/$project/knowledge' },
  { key: 'datasets', label: 'Datasets', Icon: FileSpreadsheet, text: 'text-hue-release', tint: 'bg-hue-release/16', to: '/p/$project/datasets' },
  { key: 'apis', label: 'APIs', Icon: Plug, text: 'text-hue-apps', tint: 'bg-hue-apps/16', to: '/p/$project/apis' },
  { key: 'settings', label: 'Settings', Icon: Settings2, text: 'text-hue-home', tint: 'bg-hue-home/16', overlay: { sheet: 'settings', section: 'general' } },
]
