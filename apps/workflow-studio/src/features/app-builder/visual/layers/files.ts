import { useMemo } from 'react'
import { isClientPath } from '../../lib/appModel'
import { useAppDocumentStore } from '../../stores'
import type { FileMap } from '../source'

/** The app's files as a FileMap, one object per document version (so the layers memo hits until the source changes). */
export function useLayerFiles(): FileMap {
  const docFiles = useAppDocumentStore((s) => s.files)
  return useMemo<FileMap>(() => Object.fromEntries((docFiles ?? []).filter((f) => !isClientPath(f.path)).map((f) => [f.path, f.content])), [docFiles])
}
