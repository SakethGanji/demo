import * as React from "react"
import { Popover as PopoverPrimitive } from '@base-ui/react/popover'
import { cn } from '@/shared/lib/utils'
import { useLiquidGlassRef } from '@/shared/lib/liquid-glass'

function Popover({ ...props }: PopoverPrimitive.Root.Props) {
  return <PopoverPrimitive.Root data-slot="popover" {...props} />
}

const PopoverTrigger = React.forwardRef<HTMLButtonElement, PopoverPrimitive.Trigger.Props>(function PopoverTrigger(props, ref) {
  return <PopoverPrimitive.Trigger ref={ref} data-slot="popover-trigger" {...props} />
})

function PopoverClose({ ...props }: PopoverPrimitive.Close.Props) {
  return <PopoverPrimitive.Close data-slot="popover-close" {...props} />
}

function PopoverContent({
  className,
  side = 'bottom',
  sideOffset = 6,
  align = 'start',
  alignOffset = 0,
  anchor,
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<PopoverPrimitive.Positioner.Props, 'align' | 'alignOffset' | 'side' | 'sideOffset' | 'anchor'>) {
  // liquid glass on the popover edge (Chromium; fixed size while open)
  const popupRef = useLiquidGlassRef({ radius: 8, bezel: 9, strength: 14, extra: 'blur(28px) saturate(180%)' })
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
        anchor={anchor}
        className="isolate z-50"
      >
        <PopoverPrimitive.Popup
          ref={popupRef}
          data-slot="popover-content"
          className={cn(
            'z-50 w-72 origin-(--transform-origin) glass-overlay p-3 text-[13px] text-popover-foreground outline-hidden duration-(--d-fast) ease-(--ease-out) data-open:animate-in data-open:fade-in-0 data-open:zoom-in-[.97] data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-[.97]',
            className,
          )}
          {...props}
        />
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  )
}

export { Popover, PopoverTrigger, PopoverContent, PopoverClose }
