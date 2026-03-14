import { MoreHorizontal, AppWindow, Clock } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/shared/components/ui/alert-dialog'

import type { AppSummary } from '../hooks/useApps'
import { useAppActions } from '../hooks/useAppActions'
import { formatRelative } from '../lib/formatDate'

interface AppCardProps {
  app: AppSummary
}

export function AppCard({ app }: AppCardProps) {
  const {
    isDeleting,
    deleteDialogOpen,
    setDeleteDialogOpen,
    handleOpen,
    handleDelete,
  } = useAppActions(app)

  return (
    <>
      <div
        className="group cursor-pointer rounded-xl bg-card hover:shadow-md hover:-translate-y-0.5 transition-all duration-200"
        onClick={handleOpen}
      >
        {/* Thumbnail */}
        <div className="relative overflow-hidden rounded-t-xl">
          <div className="aspect-[16/10] w-full bg-muted/20 flex items-center justify-center">
            <AppWindow size={32} className="text-muted-foreground/30" />
          </div>
          <div className="absolute top-2.5 right-2.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <DropdownMenu>
              <DropdownMenuTrigger onClick={(e) => e.stopPropagation()}>
                <Button
                  variant="secondary"
                  size="icon-sm"
                  className="h-7 w-7 rounded-full shadow-sm"
                >
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                <DropdownMenuItem onClick={handleOpen}>
                  Open in Builder
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="text-destructive"
                  onClick={(e) => {
                    e.stopPropagation()
                    setDeleteDialogOpen(true)
                  }}
                >
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* Info */}
        <div className="px-4 py-4">
          <h3 className="font-medium text-[13px] leading-snug truncate mb-1.5">
            {app.name}
          </h3>
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Clock size={11} />
            <span>{formatRelative(app.updatedAt)}</span>
          </div>
        </div>
      </div>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete app</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{app.name}&quot;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={isDeleting}>
              {isDeleting ? 'Deleting...' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
