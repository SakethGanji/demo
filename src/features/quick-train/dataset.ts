// A dataset as the New Model wizard reads it: the adapter for profiles from the API.

import type { DatasetDetail, Profile } from '@/shared/api/types'

export type Col = { name: string; type: 'text' | 'category' | 'number' | 'id' | 'date'; sample: string; missing?: number }

export type Dataset = {
  name: string; rows: number; cols: Col[]; target: string; classes: { name: string; share: number }[]; kind: 'text' | 'tabular'; note: string
  /** Absent only for EMPTY_DATASET, before one is loaded. */
  id?: string
  profile?: Profile
}

/** A dataset profiled by the API, in the shape the pages already use. */
export function fromServer(d: DatasetDetail): Dataset {
  const p = d.profile
  return {
    id: d.id, profile: p, name: d.filename, rows: p.rows, kind: p.kind, note: d.note,
    target: p.target ?? '',
    cols: p.columns.map((c) => ({ name: c.name, type: c.type, sample: c.example.slice(0, 48), missing: c.missing })),
    classes: p.classes.map((c) => ({ name: c.name, share: c.share })),
  }
}

/** Placeholder while the wizard's dataset is loading. */
export const EMPTY_DATASET: Dataset = { name: '', rows: 0, cols: [], target: '', classes: [], kind: 'tabular', note: '' }
