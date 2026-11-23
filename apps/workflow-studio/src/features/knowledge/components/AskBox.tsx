/**
 * "Ask the graph": type what you want to build and the concepts the AI builder would hear light up, with
 * everything that implements them. Lexical only (`/knowledge/understand`), so it is instant and saves nothing.
 */
import { AnimatePresence, motion } from 'motion/react'
import { Sparkles, X } from 'lucide-react'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/shared/components/ui/input-group'
import { Spinner } from '@/shared/components/ui/spinner'
import { dur, ease } from '@/shared/motion/tokens'
import { pluralize } from '@/features/project/lib/format'
import { ConceptChip } from '../kit/ConceptChip'
import type { KgNode } from '../lib/types'

export function AskBox({
  value, onChange, pending, concepts, implementers, unmatched, onPick, off,
}: {
  value: string
  onChange: (v: string) => void
  pending: boolean
  /** matched concept nodes, the engine's order */
  concepts: KgNode[]
  implementers: number
  /** the engine heard something but none of it is in this graph */
  unmatched: boolean
  onPick: (id: string) => void
  off: boolean
}) {
  const show = value.trim().length > 0 && (concepts.length > 0 || unmatched)
  return (
    <div className="flex w-full flex-col items-center gap-2">
      <AnimatePresence initial={false}>
        {show && (
          <motion.div
            key="hits"
            initial={off ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 6, transition: { duration: dur.fast } }}
            transition={{ duration: dur.base, ease: ease.out }}
            className="glass-chrome flex max-w-full flex-wrap items-center justify-center gap-1 rounded-panel px-2.5 py-1.5"
            data-testid="knowledge-ask-hits"
            data-count={concepts.length}
          >
            {concepts.length ? (
              <>
                <span className="mr-0.5 text-[12px] text-ai-text">It hears</span>
                {concepts.map((c) => (
                  <button key={c.id} type="button" onClick={() => onPick(c.id)} className="rounded-full outline-none focus-visible:shadow-focus" aria-label={`Show ${c.label}`}>
                    <ConceptChip conceptKey={c.key} label={c.label} tone="confirmed" data-testid={`knowledge-ask-concept-${c.id}`} />
                  </button>
                ))}
                <span className="ml-1 text-[12px] text-muted-foreground">
                  {implementers ? `${pluralize(implementers, 'thing')} can build it` : 'nothing implements it yet'}
                </span>
              </>
            ) : (
              <span className="text-[12px] text-muted-foreground">No concept in this graph matches that yet.</span>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      <InputGroup className="glass-chrome h-10 w-full rounded-full bg-transparent px-1.5">
        <InputGroupAddon>
          {pending ? <Spinner className="size-4 text-ai-text" /> : <Sparkles className="size-4 text-ai-text" />}
        </InputGroupAddon>
        <InputGroupInput
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape' && value) {
              e.stopPropagation()
              onChange('')
            }
          }}
          placeholder="Ask the graph: what would it take to email a weekly sales report?"
          aria-label="Ask the graph"
          data-testid="knowledge-ask"
          className="text-[13px]"
        />
        {value && (
          <InputGroupAddon align="inline-end">
            <InputGroupButton size="icon-xs" aria-label="Clear the question" onClick={() => onChange('')}>
              <X />
            </InputGroupButton>
          </InputGroupAddon>
        )}
      </InputGroup>
    </div>
  )
}
