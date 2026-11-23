/** Editor-side rules for the Interface panel (DESIGN-STAGE5-UIS §3.1, §11.1). Pure, unit-tested. */
import { asComponents, screenIds, taskNodeOfScreen, type ScreenData } from './binding'
import type { Interface, InterfaceKind, PreviewData, ScreenSpec, TaskDetail, TaskSummary } from './types'

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}$/

export function validateSlug(slug: string): string | null {
  if (!slug) return 'Pick a short name for the URL'
  if (!SLUG_RE.test(slug)) return 'Use 2 to 63 lowercase letters, numbers or dashes, starting with a letter or number'
  return null
}

/** "Refund triage (UI)" -> "refund-triage-ui" */
export function slugify(name: string): string {
  const s = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63)
  return s.length >= 2 ? s : ''
}

export function kindName(i: Interface | null | undefined): 'Custom' | 'Form' | 'Chat' | 'Off' {
  if (!i) return 'Off'
  return i.kind === 'custom' ? 'Custom' : i.kind === 'form' ? 'Form' : 'Chat'
}

/** Navbar tab label: `Interface (Custom|Form|Chat|Off)` */
export function navbarLabel(i: Interface | null | undefined): string {
  return `Interface (${kindName(i)})`
}

export function interfacePath(projectSlug: string, slug: string): string {
  return `/i/${projectSlug}/${slug}`
}

/**
 * Switching kind keeps what the author already set (slug, title, description, access, app link) and takes the rest
 * (trigger node, screens) from a freshly generated interface. Screens edited earlier come back when the author
 * returns to Custom, so flipping to Form and back does not lose work.
 */
export function switchKind(current: Interface | null, generated: Interface, stashedScreens?: Record<string, ScreenSpec>): Interface {
  const next: Interface = { ...generated }
  if (current) {
    next.slug = current.slug || generated.slug
    next.title = current.title || generated.title
    if (current.description !== undefined) next.description = current.description
    if (current.access) next.access = current.access
    if (current.app_id !== undefined) next.app_id = current.app_id
    if (current.result_node !== undefined && current.result_node !== null) next.result_node = current.result_node
  }
  const kind: InterfaceKind = generated.kind
  if (kind === 'custom') {
    const keep = stashedScreens ?? current?.screens
    if (keep && Object.keys(keep).length > 0) next.screens = { ...generated.screens, ...keep }
  } else {
    delete next.screens
  }
  return next
}

export function withScreen(i: Interface, screenId: string, spec: ScreenSpec): Interface {
  return { ...i, screens: { ...(i.screens ?? {}), [screenId]: spec } }
}

/** Screens the preview can show for this interface (`start` first, then `task:*`, then `result`). */
export function previewScreenIds(i: Interface | null | undefined): string[] {
  if (!i || i.kind !== 'custom') return ['start']
  const ids = screenIds(i.screens)
  return ids.includes('start') ? ids : ['start', ...ids]
}

type TaskLike = TaskSummary | TaskDetail

/** Data for rendering `screenId` on a run: input and node outputs from the run; the matching task if any. */
export function previewScreenData(p: PreviewData | undefined, screenId: string, taskDetail?: TaskDetail | null): ScreenData {
  if (!p) return {}
  const node = taskNodeOfScreen(screenId)
  const tasks = (p.tasks ?? []) as TaskLike[]
  const found = node ? tasks.find((x) => x.node_name === node) ?? null : null
  // preview/data lists task summaries; the detail (context, form) comes from GET /api/tasks/{id}
  const t: TaskLike | null = taskDetail && found && taskDetail.id === found.id ? taskDetail : found
  const task: Record<string, unknown> = t
    ? {
        title: t.title,
        status: t.status,
        due_at: t.due_at,
        assignees: t.assignees,
        ...('context' in t ? { context: (t as TaskDetail).context, form: (t as TaskDetail).form } : {}),
      }
    : {}
  return {
    input: p.input ?? {},
    node: p.nodes ?? {},
    task,
    result: p.result ?? null,
    run: p.run ?? (p.execution_id ? { id: p.execution_id, status: p.status ?? undefined } : {}),
  }
}

export function hasComponents(spec: ScreenSpec | null | undefined): boolean {
  return (asComponents(spec) ?? []).length > 0
}

/** Short label for the run picker: "Live data · run #a1b2c3". */
export function runPickerLabel(id: string): string {
  const short = id.replace(/^exe_/, '').slice(0, 8)
  return `Live data · run #${short}`
}

export function interfaceSignature(i: Interface | null | undefined): string {
  return JSON.stringify(i ?? null)
}
