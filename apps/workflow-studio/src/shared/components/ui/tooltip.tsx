/**
 * Tooltips (DESIGN §9): glass overlay, 12px, no arrow, a 2px rise. One `TooltipProvider delay={500}
 * timeout={300}` is mounted in `__root.tsx`: tooltips appear after 500ms, then instantly for the next 300ms.
 * `Tip` is the short form: `<Tip label="Copy" kbd="⌘C"><Button …/></Tip>`.
 */
import * as React from "react"
import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip"

import { cn } from "@/shared/lib/utils"

function TooltipProvider({
  delay = 500,
  timeout = 300,
  ...props
}: TooltipPrimitive.Provider.Props) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delay={delay}
      timeout={timeout}
      {...props}
    />
  )
}

function Tooltip({ ...props }: TooltipPrimitive.Root.Props) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

const TooltipTrigger = React.forwardRef<HTMLButtonElement, TooltipPrimitive.Trigger.Props>(function TooltipTrigger(props, ref) {
  return <TooltipPrimitive.Trigger ref={ref} data-slot="tooltip-trigger" {...props} />
})

function TooltipContent({
  className,
  side = "top",
  sideOffset = 6,
  align = "center",
  alignOffset = 0,
  children,
  ...props
}: TooltipPrimitive.Popup.Props &
  Pick<
    TooltipPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  >) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Positioner
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
        className="isolate z-50"
      >
        <TooltipPrimitive.Popup
          data-slot="tooltip-content"
          className={cn(
            "z-50 inline-flex w-fit max-w-[260px] origin-(--transform-origin) items-center gap-1.5 glass-overlay rounded-ctl px-2 py-1 text-[12px] leading-4 text-popover-foreground has-data-[slot=kbd]:pr-1 duration-(--d-fast) ease-(--ease-out) data-[side=bottom]:slide-in-from-top-[2px] data-[side=left]:slide-in-from-right-[2px] data-[side=right]:slide-in-from-left-[2px] data-[side=top]:slide-in-from-bottom-[2px] **:data-[slot=kbd]:bg-recess **:data-[slot=kbd]:shadow-none data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
            className
          )}
          {...props}
        >
          {children}
        </TooltipPrimitive.Popup>
      </TooltipPrimitive.Positioner>
    </TooltipPrimitive.Portal>
  )
}

/**
 * A trigger with a tooltip. `children` must be a single element that accepts a ref (a Button, a Link, a
 * plain element); it becomes the trigger through Base UI's `render` composition, so no extra wrapper is added.
 */
function Tip({
  label,
  kbd,
  side,
  align,
  children,
  disabled,
}: {
  label: React.ReactNode
  kbd?: string
  side?: TooltipPrimitive.Positioner.Props["side"]
  align?: TooltipPrimitive.Positioner.Props["align"]
  children: React.ReactElement
  disabled?: boolean
}) {
  if (disabled || label == null || label === "") return children
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent side={side} align={align}>
        <span>{label}</span>
        {kbd && (
          <kbd
            data-slot="kbd"
            className="inline-flex h-4 min-w-4 items-center justify-center rounded-kbd px-1 text-[10.5px] font-medium leading-none text-muted-foreground"
          >
            {kbd}
          </kbd>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider, Tip }
