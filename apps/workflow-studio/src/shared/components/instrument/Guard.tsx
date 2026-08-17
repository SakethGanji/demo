import * as React from "react"

import { cn } from "@/shared/lib/utils"

/**
 * Guard — a caveat attached to the thing it qualifies.
 *
 * This is INSTRUMENT rule 8's second half made concrete. A guard note inside a
 * card must NOT become a second box: "never nest a container inside a
 * container" was identified as the loudest generated-looking tell in the whole
 * language. So a guard is a left rule and an indent — it reads as *attached to*
 * its parent rather than as a sibling panel floating inside one.
 *
 * It exists as a shared primitive because it was independently re-invented in
 * three different lenses within one build, each with slightly different padding
 * and a different inset shadow. That is the signal for promotion.
 *
 * `tone` is deliberately limited and deliberately quiet:
 *
 *  - `neutral` — the default, and correct for almost everything. A caveat is
 *    usually a fact about how the system works ("scope_type is derived, never
 *    sent"), not an alarm.
 *  - `warning` / `critical` — only when the guard describes a live condition
 *    with a consequence the reader must act on. These take a hue on the RULE,
 *    never on the text: rule 7 keeps severity typographic so the reserved
 *    status palette is not spent on something that merely *reads* as severe.
 */

export type GuardTone = "neutral" | "warning" | "critical"

const RULE: Record<GuardTone, string> = {
  neutral: "shadow-[inset_2px_0_0_var(--r3)]",
  warning: "shadow-[inset_2px_0_0_var(--st-warn)]",
  critical: "shadow-[inset_2px_0_0_var(--st-crit)]",
}

function Guard({
  tone = "neutral",
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & { tone?: GuardTone }) {
  return (
    <div
      data-slot="guard"
      data-tone={tone}
      className={cn(
        "py-1 pl-2.5 text-footnote text-muted-foreground",
        RULE[tone],
        className
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export { Guard }
