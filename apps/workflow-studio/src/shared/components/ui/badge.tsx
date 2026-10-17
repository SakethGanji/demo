import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/shared/lib/utils"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-chip border-0 px-2 py-0.5 text-[12px] font-[550] whitespace-nowrap focus-visible:shadow-focus has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-(--primary-hover)",
        secondary: "bg-raise text-foreground shadow-raise-sm",
        destructive: "bg-destructive-soft text-destructive [a]:hover:bg-destructive/18",
        error: "bg-destructive-soft text-destructive",
        outline: "bg-recess text-muted-foreground [a]:hover:text-foreground",
        ghost: "text-muted-foreground hover:bg-(--hover-fill)",
        link: "text-primary underline-offset-4 hover:underline",
        success: "bg-success/11 text-success-text",
        warning: "bg-warning/13 text-warning-text",
        ai: "bg-ai-soft text-ai-text",
        neutral: "bg-recess text-muted-foreground",
        stub: "bg-transparent px-0.5 text-warning-text",
        primary: "bg-brand-soft text-method-post",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      variant,
    },
  })
}

export { Badge, badgeVariants }
