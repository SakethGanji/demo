import * as React from "react"
import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/shared/lib/utils"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-ctl border-0 text-[13px] font-medium whitespace-nowrap outline-none select-none focus-visible:shadow-focus disabled:pointer-events-none disabled:opacity-50 aria-invalid:shadow-[0_0_0_2px_var(--destructive-soft)] [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5 [&_svg]:stroke-[1.75]",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-[var(--btn-primary-top)] hover:bg-(--primary-hover)",
        outline:
          "bg-recess text-foreground hover:bg-(--hover-fill) aria-expanded:bg-(--hover-fill)",
        secondary:
          "bg-(--hover-fill) text-foreground hover:bg-foreground/14 aria-expanded:bg-foreground/14",
        ghost:
          "text-foreground/85 hover:bg-(--hover-fill) hover:text-foreground aria-expanded:bg-(--hover-fill) aria-expanded:text-foreground",
        destructive:
          "bg-destructive-soft text-destructive hover:bg-destructive/18",
        link: "text-primary underline-offset-4 hover:underline",
        ai: "bg-primary text-primary-foreground shadow-[var(--btn-primary-top)] hover:bg-(--primary-hover) [&_svg]:text-primary-foreground",
      },
      size: {
        default:
          "h-8 gap-1.5 px-3 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2.5",
        xs: "h-6 gap-1 rounded-chip px-2 text-xs in-data-[slot=button-group]:rounded-ctl has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1.5 rounded-ctl px-2.5 text-[12.5px] in-data-[slot=button-group]:rounded-ctl has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 gap-1.5 px-3.5 has-data-[icon=inline-end]:pr-3 has-data-[icon=inline-start]:pl-3",
        icon: "size-8",
        "icon-xs":
          "size-6 rounded-chip in-data-[slot=button-group]:rounded-ctl [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-7 rounded-ctl in-data-[slot=button-group]:rounded-ctl [&_svg:not([class*='size-'])]:size-3.5",
        "icon-lg": "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

/**
 * forwardRef so it can be a Base UI `render` target (triggers pass refs). When `render` swaps
 * the element (e.g. a router <Link>), native button semantics are off unless asked for.
 */
const Button = React.forwardRef<
  HTMLElement,
  ButtonPrimitive.Props & VariantProps<typeof buttonVariants>
>(function Button({ className, variant = "default", size = "default", render, nativeButton, ...props }, ref) {
  return (
    <ButtonPrimitive
      ref={ref as React.Ref<HTMLButtonElement>}
      data-slot="button"
      data-variant={variant ?? undefined}
      className={cn(buttonVariants({ variant, size, className }))}
      render={render}
      nativeButton={nativeButton ?? (render ? false : undefined)}
      {...props}
    />
  )
})

export { Button, buttonVariants }
