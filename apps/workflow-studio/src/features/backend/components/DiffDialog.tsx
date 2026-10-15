/** Side-by-side diff (`@codemirror/merge`), used by the composer Diff and the concurrent-edit dialog. */
import { useEffect, useRef } from 'react'
import { MergeView } from '@codemirror/merge'
import { EditorView } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { python } from '@codemirror/lang-python'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'

function MergePane({ a, b }: { a: string; b: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const ext = [python(), EditorView.editable.of(false), EditorState.readOnly.of(true), EditorView.theme({ '&': { fontSize: '12px' }, '.cm-scroller': { fontFamily: 'var(--font-mono)' } })]
    const mv = new MergeView({ a: { doc: a, extensions: ext }, b: { doc: b, extensions: ext }, parent: ref.current, collapseUnchanged: { margin: 3 } })
    return () => mv.destroy()
  }, [a, b])
  return <div ref={ref} className="max-h-[60vh] overflow-auto rounded-lg border border-border bg-code-bg" data-scroll-x="allowed" data-testid="diff-view" />
}

export function DiffDialog({
  open,
  onClose,
  title,
  leftLabel,
  rightLabel,
  left,
  right,
}: {
  open: boolean
  onClose: () => void
  title: string
  leftLabel: string
  rightLabel: string
  left: string
  right: string
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-5xl" data-testid="diff-dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Left: {leftLabel} · Right: {rightLabel}
          </DialogDescription>
        </DialogHeader>
        {open && <MergePane a={left} b={right} />}
      </DialogContent>
    </Dialog>
  )
}
