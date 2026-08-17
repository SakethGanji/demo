import * as React from "react"

import { cn } from "@/shared/lib/utils"

/**
 * Severity — rule 7: severity is TYPOGRAPHIC, not coloured.
 *
 * A rule's severity ("this check is an error" / "this check is a warning") is
 * a *property* of the rule. It is true before the rule runs and stays true
 * after. Colouring it red has it impersonating *status*, which is what
 * actually happened when the check ran — and once both wear red, a screen full
 * of `error`-severity rules that all PASSED reads as a screen on fire.
 *
 * So severity is bought with weight and value: `error` is `--t1` at 600,
 * `warning` is `--t3`. That frees the entire reserved status palette for
 * things that genuinely have a state, which is the whole reason rule 7 exists.
 *
 * There is no `className` escape into a colour here by design — the component
 * accepts one, but every value it sets itself is an ink token, and a caller
 * adding `text-destructive` is doing something the language forbids and should
 * be visible in review.
 */

export type SeverityLevel = "error" | "warning"

const LEVEL: Record<SeverityLevel, string> = {
  error: "font-semibold text-foreground",
  warning: "text-muted-foreground",
}

function Severity({
  level,
  className,
  children,
  ...props
}: React.ComponentProps<"span"> & { level: SeverityLevel }) {
  return (
    <span
      data-slot="severity"
      data-severity={level}
      className={cn("whitespace-nowrap", LEVEL[level], className)}
      {...props}
    >
      {children ?? level}
    </span>
  )
}

export { Severity }
