import { create } from 'zustand'
import type { ApiAppVersion } from '@/shared/lib/api'
import type { AppFile } from './sandbox/esbuild-bundler'

// ── Console Store ────────────────────────────────────────────────────────────

type LogLevel = 'info' | 'warn' | 'error' | 'success'

export interface ConsoleEntry {
  id: number
  timestamp: number
  level: LogLevel
  source: string
  message: string
  detail?: unknown
}

let entryId = 0

interface ConsoleState {
  entries: ConsoleEntry[]
  log: (level: LogLevel, source: string, message: string, detail?: unknown) => void
  clear: () => void
}

export const useConsoleStore = create<ConsoleState>()((set) => ({
  entries: [],

  log: (level, source, message, detail) => {
    set((state) => ({
      entries: [
        ...state.entries.slice(-199), // keep last 200
        {
          id: ++entryId,
          timestamp: Date.now(),
          level,
          source,
          message,
          detail,
        },
      ],
    }))
  },

  clear: () => {
    set({ entries: [] })
  },
}))

// ── App Document Store ───────────────────────────────────────────────────────

interface AppDocumentState {
  /** Multi-file app sources */
  files: AppFile[] | null
  /** Entry-point source code (App.tsx content, for backwards compat) */
  sourceCode: string | null
  /** Current version info from the backend */
  currentVersion: ApiAppVersion | null

  setFiles: (files: AppFile[]) => void
  setSourceCode: (source: string) => void
  setCurrentVersion: (version: ApiAppVersion | null) => void
  reset: () => void
}

export const useAppDocumentStore = create<AppDocumentState>()((set) => ({
  files: null,
  sourceCode: null,
  currentVersion: null,

  setFiles: (files) => {
    const entry = files.find((f) => /^(src\/)?[Aa]pp\.tsx$/.test(f.path))
    set({ files, sourceCode: entry?.content ?? files[0]?.content ?? null })
  },

  setSourceCode: (source) => {
    set({
      sourceCode: source,
      files: [{ path: 'App.tsx', content: source }],
    })
  },

  setCurrentVersion: (version) => {
    set({ currentVersion: version })
  },

  reset: () => {
    set({ files: null, sourceCode: null, currentVersion: null })
  },
}))
