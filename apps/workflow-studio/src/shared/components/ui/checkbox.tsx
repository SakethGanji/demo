"use client"

import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox"
import { CheckIcon, MinusIcon } from "lucide-react"

import { cn } from "@/shared/lib/utils"

/**
 * Checkbox.
 *
 * Checked state is repeated state — a projection list has one per column — so
 * it takes value (`--primary`, which is near-white in dark and near-black in
 * light) rather than the accent. Same reasoning as Tabs.
 *
 * The box is a recessed well when unchecked, matching the material treatment
 * INSTRUMENT gives inputs. `indeterminate` renders a minus, so a partial
 * selection is never shown as either checked or unchecked.
 */
function Checkbox({ className, ...props }: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "peer size-4 shrink-0 cursor-pointer rounded-[3px] bg-[var(--s0)] shadow-[inset_0_1px_2px_rgba(0,0,0,.35)] ring-1 ring-border transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring data-checked:bg-primary data-checked:ring-transparent data-indeterminate:bg-primary data-indeterminate:ring-transparent disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        data-slot="checkbox-indicator"
        className="flex items-center justify-center text-primary-foreground data-unchecked:hidden"
        render={(indicatorProps, state) => (
          <span {...indicatorProps}>
            {state.indeterminate ? (
              <MinusIcon className="size-3" strokeWidth={3} />
            ) : (
              <CheckIcon className="size-3" strokeWidth={3} />
            )}
          </span>
        )}
      />
    </CheckboxPrimitive.Root>
  )
}

export { Checkbox }
