/** The six start cards ("Where do you want to start?"), shared by the setup guide and "Start something new". */
import type { StartKind } from '../../lib/nextSteps'

export interface StartCard {
  kind: StartKind
  title: string
  blurb: string
}

export const START_CARDS: StartCard[] = [
  { kind: 'interface', title: 'Interface', blurb: 'A page or app first. Routes it calls become stubs; no backend needed yet.' },
  { kind: 'backend', title: 'Backend / API', blurb: 'Describe an API and get a Python module, or write it yourself.' },
  { kind: 'workflow', title: 'Workflow', blurb: 'Steps on a canvas: a blank one, or let the AI build it with you.' },
  { kind: 'dataset', title: 'Dataset', blurb: 'Drop a CSV, Parquet or Excel file, then profile it or email a report.' },
  { kind: 'template', title: 'Template', blurb: 'Install a ready-made workflow or backend and see what it needs first.' },
  { kind: 'ai', title: 'Describe it', blurb: 'Say what you want; pick whether it becomes a workflow, a backend or an interface.' },
]
