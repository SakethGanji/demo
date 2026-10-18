/**
 * Kinds of node in the capability graph and what each one looks like. Shared by the Knowledge page and the Build sidebar
 * (PLAN-CAPGRAPH-UI §1.1). Shape means kind; colour only tints icons.
 */
import {
  AppWindow, Box, FileText, GitBranch, LayoutGrid, LayoutTemplate, Lightbulb, PanelsTopLeft, Plug, Shapes, ShieldCheck, Sparkles,
  UserRound, Workflow, type LucideIcon,
} from 'lucide-react'
import type { EntityHue } from '@/shared/components/ui/surface'

export type KgKind = 'concept' | 'capability' | 'role' | 'pattern' | 'app'
export const KIND_ORDER: KgKind[] = ['concept', 'role', 'capability', 'pattern', 'app']
export const KIND_LABEL: Record<KgKind, { one: string; many: string }> = {
  concept: { one: 'concept', many: 'concepts' },
  role: { one: 'role', many: 'roles' },
  capability: { one: 'capability', many: 'capabilities' },
  pattern: { one: 'pattern', many: 'patterns' },
  app: { one: 'app', many: 'apps' },
}

const CONCEPT_ICON: Record<string, LucideIcon> = { process: Workflow, entity: Box, control: ShieldCheck, artifact: FileText }
const CAPABILITY_ICON: Record<string, LucideIcon> = {
  node_type: Shapes,
  api_operation: Plug,
  workflow: Workflow,
  subworkflow: GitBranch,
  interface: PanelsTopLeft,
  screen_component: LayoutGrid,
  template: LayoutTemplate,
}

/** Icon per kind; for a concept the subkind is its `props.kind` (process, entity, control, artifact). */
export function kindIcon(kind: KgKind, subkind?: string | null): LucideIcon {
  switch (kind) {
    case 'concept': return (subkind && CONCEPT_ICON[subkind]) || Lightbulb
    case 'role': return UserRound
    case 'capability': return (subkind && CAPABILITY_ICON[subkind]) || Shapes
    case 'pattern': return Sparkles
    case 'app': return AppWindow
  }
}

/** Entity hue per kind. Icons only; fills stay neutral. */
export function kindHue(kind: KgKind, subkind?: string | null): EntityHue {
  switch (kind) {
    case 'concept': return 'ai'
    case 'role': return 'secrets'
    case 'pattern': return 'release'
    case 'app': return 'apps'
    case 'capability':
      if (subkind === 'api_operation') return 'backend'
      if (subkind === 'workflow' || subkind === 'subworkflow') return 'flow'
      if (subkind === 'node_type') return 'home'
      return 'apps'
  }
}

/** `kg-node-<key>` with every ':' replaced by '-' (CONTRACTS §6.3). */
export const nodeTestId = (key: string) => `kg-node-${key.replaceAll(':', '-')}`

/** Narrows an engine kind string. Unknown kinds fall back to `capability`. */
export function asKind(k: string): KgKind {
  return (KIND_ORDER as string[]).includes(k) ? (k as KgKind) : 'capability'
}
