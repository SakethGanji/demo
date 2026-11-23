/** The lucide icon drawn inside a node (and beside it in lists): by subkind when it has one, else by kind. */
import {
  AppWindow, Box, Boxes, Circle, LayoutTemplate, Lightbulb, PanelsTopLeft, Plug, UserRound, Workflow, type LucideIcon,
} from 'lucide-react'
import { kindGroup } from './kinds'
import type { KgNode } from './types'

const KIND_ICON: Record<string, LucideIcon> = {
  concept: Lightbulb,
  capability: Boxes,
  pattern: Workflow,
  role: UserRound,
  app: AppWindow,
  other: Circle,
}
const SUBKIND_ICON: Record<string, LucideIcon> = {
  node_type: Box,
  template: LayoutTemplate,
  screen_component: PanelsTopLeft,
  api_operation: Plug,
  subworkflow: Workflow,
}
export const iconFor = (n: Pick<KgNode, 'kind' | 'subkind'>): LucideIcon =>
  (n.subkind && SUBKIND_ICON[n.subkind]) || KIND_ICON[kindGroup(n.kind)]

