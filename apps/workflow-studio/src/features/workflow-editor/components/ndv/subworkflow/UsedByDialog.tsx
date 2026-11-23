import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { UsedByList } from './UsedByList';

/** "Used by" from the workflow menu: every workflow that calls this one. */
export function UsedByDialog({ open, onOpenChange, workflowId }: { open: boolean; onOpenChange: (o: boolean) => void; workflowId: string | null }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="used-by-panel" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Used by</DialogTitle>
          <DialogDescription>Workflows that call this one as a sub-workflow.</DialogDescription>
        </DialogHeader>
        <UsedByList workflowId={workflowId} enabled={open} />
      </DialogContent>
    </Dialog>
  );
}
