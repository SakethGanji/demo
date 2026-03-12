import { MoreHorizontal, AppWindow } from 'lucide-react'

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
import { useAppActions, formatDate, formatRelative } from '../hooks/useAppActions'

interface AppListRowProps {
  app: AppSummary
}

export function AppListRow({ app }: AppListRowProps) {
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
        className="grid grid-cols-[1fr_140px_140px_40px] gap-4 px-4 py-3 border-t border-border hover:bg-muted/30 transition-colors cursor-pointer items-center"
        onClick={handleOpen}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-md bg-primary/10 flex items-center justify-center shrink-0">
            <AppWindow size={15} className="text-primary" />
          </div>
          <span className="text-sm font-medium text-foreground truncate">{app.name}</span>
        </div>
        <span className="text-xs text-muted-foreground">{formatDate(app.createdAt)}</span>
        <span className="text-xs text-muted-foreground">{formatRelative(app.updatedAt)}</span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
            <Button variant="ghost" size="icon-sm" className="h-7 w-7">
              <MoreHorizontal size={14} />
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
