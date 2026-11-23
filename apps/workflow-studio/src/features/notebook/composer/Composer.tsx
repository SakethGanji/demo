/**
 * The composer at the bottom of the column: "+" or "/" opens the block palette; ⌘↵ asks Studio (one-shot Ask,
 * greyed when capabilities.ai is false). Picking a block opens a draft form that POSTs a real cell.
 */
import { useRef, useState } from 'react'
import { Palette, type PaletteItem } from '@/shared/glass'
import { useNb } from '../context'
import { askNotice, KIND_LABEL } from '../lib/cells'
import { TOP_LEVEL_ONLY_KINDS, type CellKind } from '../types'
import { DraftCell } from './DraftCell'

const BLOCKS: { kind: CellKind; hint: string; group: string }[] = [
  { kind: 'query', hint: 'read rows from a database', group: 'Read' },
  { kind: 'http', hint: 'call an API, check the response', group: 'Read' },
  { kind: 'data', hint: 'paste rows or CSV', group: 'Read' },
  { kind: 'rule', hint: 'filter, count, compare, assert · no AI', group: 'Check' },
  { kind: 'for_each', hint: 'run steps once per row', group: 'Flow' },
  { kind: 'wait', hint: 'a duration, or until a query holds', group: 'Flow' },
  { kind: 'workflow', hint: 'run an existing workflow', group: 'Flow' },
  { kind: 'change', hint: 'update rows · shown as a diff, applied on approval', group: 'Write' },
  { kind: 'store', hint: 'save rows to a project table', group: 'Write' },
  { kind: 'browser', hint: 'run recorded browser steps', group: 'Browser' },
  { kind: 'note', hint: 'text for the record', group: 'Other' },
]

export function Composer({ draft, setDraft, inside, setInside, onImport }: {
  draft: { kind: CellKind; parent: string | null } | null
  setDraft: (d: { kind: CellKind; parent: string | null } | null) => void
  /** a for-each the palette is adding a step inside (top-level-only kinds are hidden) */
  inside: string | null
  setInside: (id: string | null) => void
  /** open the API import panel (cURL / API catalog) */
  onImport?: () => void
}) {
  const { caps, capsMissing, actions, me, fail, notify, live } = useNb()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  // a block was picked: focus moves into the new draft's first field; otherwise (Escape) back to the composer
  const picked = useRef(false)
  const focusAfter = () => {
    const was = picked.current
    picked.current = false
    if (was) {
      // Base UI restores focus after the close settles; if the user already clicked into the draft, don't yank it back
      if (document.activeElement?.closest('[data-testid="nb-draft"]')) return false
      const first = document.querySelector<HTMLElement>('[data-testid="nb-draft"] :is(input, textarea, select):not([disabled])')
      if (first) return first
    }
    return inputRef.current
  }
  const aiOn = !!caps?.ai.available
  const aiReason = capsMissing ? 'AI status is not reported by this engine yet' : caps?.ai.reason || 'AI is not available'
  const browserOn = caps ? caps.browser.available : true
  const browserReason = caps?.browser.reason || 'no browser runner is connected'

  const items: PaletteItem[] = BLOCKS.filter((b) => !inside || !TOP_LEVEL_ONLY_KINDS.includes(b.kind)).map((b) => {
    const disabled = b.kind === 'browser' && !browserOn
    return {
      id: b.kind,
      label: KIND_LABEL[b.kind],
      hint: b.hint,
      group: b.group,
      keywords: b.kind,
      disabled,
      reason: disabled ? browserReason : undefined,
      onSelect: () => {
        picked.current = true
        setOpen(false)
        setDraft({ kind: b.kind, parent: inside })
        setInside(null)
      },
    }
  })
  if (!inside && onImport) items.push({
    id: 'import-http',
    label: 'Import API requests',
    hint: 'from a cURL command or the API catalog',
    group: 'Read',
    keywords: 'curl openapi api catalog import postman',
    onSelect: () => {
      setOpen(false)
      onImport()
    },
  })
  if (!inside) items.push({
    id: 'ask',
    label: 'Ask Studio',
    hint: 'describe it; Studio writes the cells once',
    group: 'AI',
    disabled: !aiOn,
    reason: aiOn ? undefined : aiReason,
    onSelect: () => {
      setOpen(false)
      void ask()
    },
  })

  const ask = async () => {
    const t = text.trim()
    if (!t || !aiOn) return
    actions.ask.mutate(
      { text: t, by: me },
      {
        onSuccess: (r) => {
          setText('')
          notify(...askNotice(r))
        },
        onError: fail,
      },
    )
  }

  return (
    <>
      {draft && <DraftCell kind={draft.kind} parent={draft.parent} onDone={() => setDraft(null)} />}
      <div className="nb-composer" data-testid="nb-composer">
        <button type="button" className="nb-plus" aria-label="Add a block" onClick={() => setOpen(true)} data-testid="nb-add">
          +
        </button>
        <input
          ref={inputRef}
          value={text}
          aria-label="Add a block or ask Studio"
          placeholder={aiOn ? 'Add a block — / for Query, For each, HTTP… or ⌘↵ to ask Studio' : 'Add a block — / for Query, For each, HTTP…'}
          onChange={(e) => {
            const v = e.target.value
            if (v === '/') {
              setOpen(true)
              setText('')
              return
            }
            setText(v)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void ask()
            }
          }}
        />
        <span className="nb-kbd" aria-hidden>/</span>
        <button
          type="button"
          className="gl-btn"
          data-size="sm"
          disabled={!aiOn || !text.trim() || actions.ask.isPending || live}
          title={aiOn ? 'Ask Studio (⌘↵)' : aiReason}
          onClick={() => void ask()}
          data-testid="nb-ask"
        >
          ✦ Ask Studio <span className="nb-kbd">⌘↵</span>
        </button>
      </div>
      <Palette
        open={open || !!inside}
        onOpenChange={(o) => {
          setOpen(o)
          if (!o) setInside(null)
        }}
        items={items}
        finalFocus={focusAfter}
        placeholder={inside ? 'Add a step inside the for-each…' : 'Add a block…'}
        data-testid="nb-palette"
      />
    </>
  )
}
