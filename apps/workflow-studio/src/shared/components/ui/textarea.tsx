import * as React from "react"

import { cn } from "@/shared/lib/utils"

const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      data-slot="textarea"
      className={cn(
        "w-full min-w-0 field px-2.5 py-2 text-[13px] outline-none placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:shadow-[0_0_0_2px_var(--destructive-soft)]",
        className
      )}
      {...props}
    />
  )
})

export { Textarea }
