/**
 * Toasts (DESIGN §8): sonner, restyled as glass overlays. Bottom-right, at most 3, the tone shown as a dot
 * (no coloured fills), a busy toast spins until it is updated by id. Mounted once in `__root.tsx`.
 */
import { Toaster as Sonner } from 'sonner'

const dot = (cls: string) => <span className={`mt-[5px] inline-block size-2 shrink-0 rounded-full ${cls}`} />

export function Toaster() {
  return (
    <Sonner
      position="bottom-right"
      visibleToasts={3}
      closeButton
      gap={8}
      offset={16}
      icons={{
        success: dot('bg-success'),
        error: dot('bg-destructive'),
        warning: dot('bg-warning'),
        info: dot('bg-primary'),
        loading: <span className="mt-[3px] inline-block size-3 shrink-0 animate-spin rounded-full border-[1.5px] border-muted-foreground/30 border-t-primary" />,
      }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            'group/toast pointer-events-auto relative flex w-(--width) items-start gap-2.5 glass-overlay px-3.5 py-3 text-[13px] text-popover-foreground',
          title: 'font-medium leading-5',
          description: 'mt-0.5 text-[12.5px] leading-[18px] text-muted-foreground',
          content: 'flex min-w-0 flex-1 flex-col',
          icon: 'flex shrink-0',
          actionButton:
            'ml-auto inline-flex h-6 shrink-0 items-center rounded-chip px-2 text-[12px] font-medium text-foreground hover:bg-(--hover-fill)',
          cancelButton:
            'inline-flex h-6 shrink-0 items-center rounded-chip px-2 text-[12px] text-muted-foreground hover:bg-(--hover-fill)',
          closeButton:
            'absolute! -top-1.5! -left-1.5! size-5! rounded-full! border-0! bg-raise! text-muted-foreground! shadow-raise-sm! opacity-0 transition-opacity group-hover/toast:opacity-100 hover:text-foreground!',
        },
      }}
    />
  )
}
