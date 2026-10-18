/**
 * A concept as a chip (PLAN-CAPGRAPH-UI §1.3). The tone is carried by the fill alone: no border, no ring.
 * confirmed = AI-soft fill, suggested = recess, implied = recess plus "implied".
 */
import { Link } from '@tanstack/react-router'
import { motion } from 'motion/react'
import { Lightbulb } from 'lucide-react'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { dur, ease, spring } from '@/shared/motion/tokens'
import { knowledgeLink, type KnowledgeSearch } from './links'

export interface ConceptChipProps {
  conceptKey: string
  label: string
  tone?: 'confirmed' | 'suggested' | 'implied'
  /** Tip text: the definition or the evidence */
  hint?: string
  /** renders a Link; else a span */
  href?: KnowledgeSearch & { projectSlug: string }
  size?: 'xs' | 'sm'
  'data-testid'?: string
  /** optional shared-layout id */
  layoutId?: string
}

export function ConceptChip({ conceptKey, label, tone = 'confirmed', hint, href, size = 'xs', layoutId, ...rest }: ConceptChipProps) {
  const cls = cn(
    'inline-flex max-w-full items-center gap-1 rounded-chip px-1.5 text-[12px] leading-none whitespace-nowrap [&_svg]:size-3 [&_svg]:shrink-0',
    size === 'xs' ? 'h-5' : 'h-6',
    tone === 'confirmed' ? 'bg-(--ai-soft) text-ai-text' : 'bg-recess text-muted-foreground',
    href && 'hover:text-foreground',
  )
  const inner = (
    <>
      <Lightbulb aria-hidden />
      <span className="truncate">{label}</span>
      {tone === 'implied' && <span className="text-faint"> · implied</span>}
    </>
  )
  const body = href ? (
    <Link {...knowledgeLink(href.projectSlug, { tab: href.tab, focus: href.focus ?? conceptKey, depth: href.depth })} className={cn(cls, 'outline-none focus-visible:shadow-focus')}>
      {inner}
    </Link>
  ) : (
    <span className={cls}>{inner}</span>
  )
  return (
    <Tip label={hint}>
      <motion.span
        layout
        layoutId={layoutId}
        className="inline-flex max-w-full"
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1, transition: spring.snap }}
        exit={{ opacity: 0, scale: 0.9, transition: { duration: dur.fast, ease: ease.in } }}
        data-concept={conceptKey}
        {...rest}
      >
        {body}
      </motion.span>
    </Tip>
  )
}
