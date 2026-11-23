import { ArrowLeftRight, FileCode2, FileSpreadsheet, LayoutDashboard, Table2, Workflow } from 'lucide-react'
import type { LaneKey, ObjKind } from '../lib/model'

const ICON = { app: LayoutDashboard, route: ArrowLeftRight, module: FileCode2, workflow: Workflow, table: Table2, dataset: FileSpreadsheet }
const LANE_KIND: Record<LaneKey, ObjKind> = { apps: 'app', routes: 'route', modules: 'module', workflows: 'workflow', data: 'table' }

/** The lucide icon for an object kind (or a lane's first kind). */
export function KindIcon({ kind, lane, className }: { kind?: ObjKind; lane?: LaneKey; className?: string }) {
  const Icon = ICON[kind ?? LANE_KIND[lane ?? 'apps']]
  return <Icon className={className} aria-hidden />
}
