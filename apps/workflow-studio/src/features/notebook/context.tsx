/** Shared state of one open notebook page: data, actions, the workspace and per-viewer folds. */
import { createContext, useContext } from 'react'
import type { Connection } from '@/features/connections/connectionsTypes'
import type { useNotebookActions } from './hooks'
import type { WsTab } from './lib/cells'
import type { Capabilities, Cell, Notebook } from './types'

export interface WsState {
  open: boolean
  tab: WsTab
  cellId: string | null
}

export interface NbCtx {
  nb: Notebook
  pid: string
  slug: string
  caps: Capabilities | null
  /** capabilities route answered 501 (not built yet) */
  capsMissing: boolean
  actions: ReturnType<typeof useNotebookActions>
  me: string
  live: boolean
  runningId: string | null
  numberOf: (cellId: string | null | undefined) => string
  ws: WsState
  openWs: (tab: WsTab, cellId?: string | null) => void
  editSpec: (cell: Cell, path: string, value: unknown) => void
  rerunFrom: (cellId: string) => void
  notify: (msg: string, tone?: 'ok' | 'need' | 'fail') => void
  fail: (e: unknown) => void
  collapsed: Record<string, boolean>
  toggleCollapsed: (cellId: string) => void
  selected: string | null
  setSelected: (cellId: string | null) => void
  connections: Connection[]
  /** open the draft composer for a new cell inside this for-each */
  addInside: (parentId: string) => void
}

const Ctx = createContext<NbCtx | null>(null)
export const NbProvider = Ctx.Provider

export function useNb(): NbCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useNb outside a notebook page')
  return v
}
