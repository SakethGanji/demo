import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { approveContract, type ContractProposal } from '../../lib/contractActions';
import ContractFieldList from './ContractFieldList';

interface ContractDialogProps {
  proposal: ContractProposal | null;
  onOpenChange: (open: boolean) => void;
}

function readApprover(): string {
  try {
    return localStorage.getItem('contract-approved-by') ?? '';
  } catch {
    return '';
  }
}

/** Shows the proposed fields and asks for the approver's name (no auth yet; it is typed). */
export default function ContractDialog({ proposal, onOpenChange }: ContractDialogProps) {
  return (
    <Dialog open={!!proposal} onOpenChange={onOpenChange}>
      <DialogContent data-testid="contract-dialog">
        {proposal && <ContractForm key={`${proposal.nodeName}:${proposal.fromExecution}`} proposal={proposal} onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  );
}

function ContractForm({ proposal, onOpenChange }: { proposal: ContractProposal; onOpenChange: (open: boolean) => void }) {
  const [approvedBy, setApprovedBy] = useState(readApprover);
  const [busy, setBusy] = useState(false);

  const approve = async () => {
    if (!approvedBy.trim()) return;
    setBusy(true);
    const res = await approveContract(proposal, approvedBy);
    setBusy(false);
    if (!res.ok) {
      toast.error('Could not approve the contract', { description: res.message });
      return;
    }
    try {
      localStorage.setItem('contract-approved-by', approvedBy.trim());
    } catch {
      /* ignore */
    }
    toast.success(`Contract approved for ${proposal.nodeName}`);
    onOpenChange(false);
  };

  const ports = Object.entries(proposal.outputs);

  return (
    <>
        <DialogHeader>
          <DialogTitle>Use as contract</DialogTitle>
          <DialogDescription>
            {`Fields seen in ${proposal.sampleCount} item${proposal.sampleCount === 1 ? '' : 's'} from the last run of ${proposal.nodeName}. Approving makes them the node's output shape for the checker and autocomplete.`}
          </DialogDescription>
        </DialogHeader>

        <div className="well max-h-64 overflow-auto p-2">
          {ports.map(([port, shape]) => (
            <div key={port}>
              {ports.length > 1 && <div className="px-2 py-1 text-[11px] font-medium text-muted-foreground">{port}</div>}
              <ContractFieldList shape={shape} />
            </div>
          ))}
        </div>

        <label className="flex flex-col gap-1 text-[12px] text-muted-foreground">
          Approved by
          <Input
            value={approvedBy}
            onChange={(e) => setApprovedBy(e.target.value)}
            placeholder="Your name"
            data-testid="contract-approved-by"
            onKeyDown={(e) => {
              if (e.key === 'Enter') void approve();
            }}
          />
        </label>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={approve} disabled={busy || !approvedBy.trim()} data-testid="contract-approve">
            Approve
          </Button>
        </DialogFooter>
    </>
  );
}
