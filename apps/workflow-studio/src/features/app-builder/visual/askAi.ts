/**
 * "Ask AI" from the visual editor's selection: the panel publishes the selected element here, the chat shows it as a context
 * chip and sends it with the next turn, and every refusal in the panel can pre-fill the chat input with a sentence about it.
 */
import { create } from 'zustand'
import { usePreviewSession } from '../stores/previewSessionStore'
import type { ElementView } from './editor'

/** What the engine gets with a turn: where the element is and its JSX source (capped). */
export interface AskSelection {
  file: string
  line: number
  name: string
  snippet: string
}

const SNIPPET_MAX = 1500

interface AskAiState {
  selection: AskSelection | null
  /** the user removed the chip for this element (`file:line`) */
  dismissed: string | null
  publish: (s: AskSelection | null) => void
  dismiss: () => void
}

const keyOf = (s: AskSelection) => `${s.file}:${s.line}`

export const useAskAi = create<AskAiState>((set, get) => ({
  selection: null,
  dismissed: null,
  publish: (selection) => {
    const cur = get().selection
    if (cur && selection && keyOf(cur) === keyOf(selection) && cur.snippet === selection.snippet) return
    set({ selection, dismissed: selection && get().dismissed === keyOf(selection) ? get().dismissed : null })
  },
  dismiss: () => {
    const s = get().selection
    if (s) set({ dismissed: keyOf(s) })
  },
}))

export function selectionOf(view: ElementView): AskSelection {
  return { file: view.file, line: view.line, name: view.name, snippet: view.source.slice(0, SNIPPET_MAX) }
}

/** The selection to send with a turn: the published one unless its chip was removed. */
export function selectionForTurn(): AskSelection | null {
  const { selection, dismissed } = useAskAi.getState()
  return selection && dismissed !== keyOf(selection) ? selection : null
}

export function chipLabel(s: AskSelection): string {
  return `${s.name} · ${s.file}:${s.line}`
}

/** `<Button> 'Submit expense' in views/MyExpenses.tsx:147` */
function describeElement(view: ElementView): string {
  const text = view.texts.find((t) => t)
  return `<${view.name}>${text ? ` '${text.slice(0, 40)}'` : ''} in ${view.file}:${view.line}`
}

/** Pre-fill the chat input (and focus it) with a short sentence about the element; `about` narrows it ("its onClick"). */
export function askAboutElement(view: ElementView, about?: string): void {
  const s = selectionOf(view)
  const store = useAskAi.getState()
  store.publish(s)
  useAskAi.setState({ dismissed: null })
  usePreviewSession.getState().setDraft(`Change ${describeElement(view)}${about ? ` (${about})` : ''} — `)
}
