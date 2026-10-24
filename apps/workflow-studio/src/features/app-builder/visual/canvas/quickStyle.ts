/**
 * The canvas toolbar's quick style: the few token controls that matter most for the selected element (size, weight and
 * colour for text; padding, gap, fill and radius for a container), each resolved the way the style panel resolves it — a
 * design prop when the component styles itself through one, otherwise a class control, otherwise left out.
 */
import type { BoxMetrics } from '../../sandbox/bridge'
import type { ElementView, PropView } from '../editor'
import { controlById, STYLE_PROPS, type Control } from '../style/tokens'

export type QuickKind = 'text' | 'box'

export const QUICK: Record<QuickKind, string[]> = {
  text: ['text-size', 'weight', 'text-color'],
  box: ['p', 'gap', 'bg', 'radius'],
}

/**
 * Text when the element lays nothing out (not flex / grid) and carries text — its own, or text the editor refused only
 * because it comes from a variable; a container otherwise.
 */
export function quickKind(view: ElementView, box: BoxMetrics | null, textWhy: string | null): QuickKind {
  if (box && /flex|grid/.test(box.display)) return 'box'
  if (view.texts.length > 0 || textWhy === null || /variable or prop/.test(textWhy ?? '')) return 'text'
  return 'box'
}

export type QuickItem = { kind: 'prop'; id: string; prop: PropView } | { kind: 'class'; id: string; control: Control }

/** An enum / bool design prop with a literal (or no) value: what the style panel offers instead of the classes it covers. */
function editableStyleProp(p: PropView): boolean {
  return p.name in STYLE_PROPS && !!p.editor && (p.editor.kind === 'enum' || p.editor.kind === 'bool') && (p.state === 'literal' || p.state === 'unset')
}

/**
 * The controls to offer for `ids`. A control a design prop covers becomes that prop (once, even when it covers two, like
 * a heading's `level`), or is dropped when the prop isn't a plain literal; class controls need the element to take classes.
 */
export function quickItems(view: ElementView, ids: string[], takesClasses: boolean): QuickItem[] {
  const out: QuickItem[] = []
  const seen = new Set<string>()
  for (const id of ids) {
    const covering = view.props.find((p) => p.editor && STYLE_PROPS[p.name]?.includes(id))
    if (covering) {
      if (editableStyleProp(covering) && !seen.has(covering.name)) {
        seen.add(covering.name)
        out.push({ kind: 'prop', id, prop: covering })
      }
      continue
    }
    const control = controlById(id)
    if (control && takesClasses) out.push({ kind: 'class', id, control })
  }
  return out
}
