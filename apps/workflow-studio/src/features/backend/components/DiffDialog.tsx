/** Side-by-side diff (`@codemirror/merge`), used by the composer Diff and the concurrent-edit dialog. */
import { useEffect, useRef } from 'react'
import { MergeView } from '@codemirror/merge'
import { EditorView } from '@codemirror/view'
import { Compartment, EditorState } from '@codemirror/state'
import { python } from '@codemirror/lang-python'
import { buildCodeTheme, watchCodeTheme } from '@/shared/components/ui/code-theme'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'

function MergePane({ a, b }: { a: string; b: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    // the shared editor theme (tokens + syntax colours); a Compartment per side so a theme flip rebuilds it in place
    const theme = () => buildCodeTheme({ fontSize: '12px', background: false, activeLine: false })
    const left = new Compartment()
    const right = new Compartment()
    const ext = (c: Compartment) => [python(), EditorView.editable.of(false), EditorState.readOnly.of(true), c.of(theme())]
    const mv = new MergeView({ a: { doc: a, extensions: ext(left) }, b: { doc: b, extensions: ext(right) }, parent: ref.current, collapseUnchanged: { margin: 3 } })
    const stopA = watchCodeTheme(mv.a, left, theme)
    const stopB = watchCodeTheme(mv.b, right, theme)
    return () => {
      stopA()
      stopB()
      mv.destroy()
    }
  }, [a, b])
  return <div ref={ref} className="well max-h-[60vh] overflow-auto" data-scroll-x="allowed" data-testid="diff-view" />
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
