/**
 * "+ Add step" on the Page view: pick what kind of step, get a fill-in-the-blank sentence ("Pull [what] from
 * [connection].") as a draft. Drafts are not saved until "Add to workflow" turns one into a real node.
 */
import { Menu } from '@base-ui/react/menu'
import { Fragment } from 'react'
import { PageStep } from '@/shared/glass/page/PageStep'
import { ValueToken } from '@/shared/glass/ValueToken'
import { Chip } from '@/shared/glass/Chip'

export type DraftKind = 'query' | 'api' | 'approval' | 'wait' | 'if' | 'each' | 'flow' | 'code'

export interface StepDraft {
  id: string
  kind: DraftKind
  values: string[]
}

interface DraftType {
  kind: DraftKind
  label: string
  hint: string
  /** `[slot]` marks a blank; the slot's text is its label. */
  sentence: string
  /** The engine node it becomes, and its parameters from the blanks. */
  node: string
  params: (v: string[]) => Record<string, unknown>
}

export const DRAFT_TYPES: DraftType[] = [
  { kind: 'query', label: 'Query a database', hint: 'Postgres', sentence: 'Pull [what] from [connection].', node: 'Postgres', params: (v) => ({ connection: v[1] ?? '', query: '' }) },
  { kind: 'api', label: 'Call an API', hint: 'HTTP', sentence: 'Call [which API] with [what], and keep [which fields].', node: 'HttpRequest', params: (v) => ({ method: 'GET', url: v[0] ?? '' }) },
  { kind: 'approval', label: 'Ask a person', hint: 'Approval', sentence: 'Ask [who] to approve [what], and wait up to [how long].', node: 'Approval', params: (v) => ({ assignees: v[0] ? [v[0]] : [], title: v[1] ?? '', due_in: v[2] ?? '' }) },
  { kind: 'wait', label: 'Wait', hint: 'Wait', sentence: 'Wait until [when], or until [what happens], whichever comes first.', node: 'Wait', params: () => ({}) },
  { kind: 'if', label: 'Decide', hint: 'If', sentence: 'If [condition], then [do what]; otherwise [do what].', node: 'If', params: (v) => ({ condition: v[0] ?? '' }) },
  { kind: 'each', label: 'Repeat for each', hint: 'Loop', sentence: 'For each [item] in [list], do the steps below.', node: 'Loop', params: () => ({ batchSize: 1 }) },
  { kind: 'flow', label: 'Run another workflow', hint: 'Sub-workflow', sentence: 'Run the workflow [which] with [inputs].', node: 'ExecuteWorkflow', params: (v) => ({ workflowId: v[0] ?? '' }) },
  { kind: 'code', label: 'Custom code', hint: 'Code', sentence: 'Run custom code: [describe what it does].', node: 'Code', params: () => ({ code: '' }) },
]

export const draftType = (k: DraftKind) => DRAFT_TYPES.find((t) => t.kind === k)!

const slots = (sentence: string) => [...sentence.matchAll(/\[([^\]]+)\]/g)].map((m) => m[1])

/** The draft as plain text, blanks filled (or `[label]`). It becomes the new node's notes. */
export function draftText(d: StepDraft): string {
  let i = 0
  return draftType(d.kind).sentence.replace(/\[([^\]]+)\]/g, (_, l: string) => d.values[i++] || `[${l}]`)
}

let seq = 0

const ICONS: Record<DraftKind, string> = { query: 'db', api: '↗', approval: '✓', wait: '◷', if: '◇', each: '↻', flow: '⧉', code: '{}' }

export function AddStepMenu({ onPick }: { onPick: (d: StepDraft) => void }) {
  return (
    <div className="gp-ins">
      <Menu.Root>
        <Menu.Trigger className="gp-ins-btn" data-testid="glass-add-step">
          + Add step
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner sideOffset={6} align="start" className="z-[70]">
            <Menu.Popup className="gl-overlay gp-palette" data-testid="glass-add-step-menu">
              <div className="gp-picker-ttl">Add a step here</div>
              {DRAFT_TYPES.map((t) => (
                <Menu.Item
                  key={t.kind}
                  className="gp-opt"
                  onClick={() => onPick({ id: `draft-${Date.now()}-${seq++}`, kind: t.kind, values: slots(t.sentence).map(() => '') })}
                  data-testid={`glass-add-${t.kind}`}
                >
                  <span className="gp-ic" aria-hidden>
                    {ICONS[t.kind]}
                  </span>
                  <span className="gp-opt-f">
                    {t.label}
                    <span className="gp-ds">{t.hint}</span>
                  </span>
                </Menu.Item>
              ))}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </div>
  )
}

function Draft({ draft, onChange, onRemove, onAdd }: { draft: StepDraft; onChange: (d: StepDraft) => void; onRemove: () => void; onAdd?: () => void }) {
  const t = draftType(draft.kind)
  const parts = t.sentence.split(/(\[[^\]]+\])/g)
  let slot = 0
  return (
    <PageStep
      status="pending"
      kind={t.hint.toLowerCase()}
      label="new step"
      data-testid="glass-draft"
      badges={<Chip size="sm" tone="warn">draft · not saved</Chip>}
      actionsAlways
      actions={
        <>
          {onAdd && (
            <button type="button" className="gl-btn" data-variant="primary" data-size="sm" onClick={onAdd} data-testid="glass-draft-add">
              Add to workflow
            </button>
          )}
          <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={onRemove} data-testid="glass-draft-remove">
            Remove
          </button>
        </>
      }
      sentence={parts.map((p, i) => {
        const m = p.match(/^\[([^\]]+)\]$/)
        if (!m) return <Fragment key={i}>{p}</Fragment>
        const ix = slot++
        return (
          <ValueToken
            key={i}
            value={draft.values[ix]}
            label={m[1]}
            placeholder={`[${m[1]}]`}
            onCommit={(v) => onChange({ ...draft, values: draft.values.map((x, j) => (j === ix ? v : x)) })}
          />
        )
      })}
    />
  )
}

AddStepMenu.Draft = Draft
