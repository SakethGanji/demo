import { useState } from 'react';
import { Trash2, Plus, KeyRound, Eye, Lock, Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { toast } from 'sonner';
import {
  useVariables,
  useCreateVariable,
  useUpdateVariable,
  useDeleteVariable,
  type VariableListItem,
} from '../../hooks/useVariablesApi';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  environment: string;
}

const KEY_RE = /^[A-Z_][A-Z0-9_]*$/;

export function EnvVariablesModal({ open, onOpenChange, environment }: Props) {
  const { data: variables, isLoading } = useVariables(environment);
  const [replacing, setReplacing] = useState<VariableListItem | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Variables — {environment}</DialogTitle>
          <DialogDescription>
            Reference these in workflow node parameters as{' '}
            <code className="font-mono text-xs">{`{{ $vars.KEY }}`}</code>. Secret values are
            write-once: to change one, replace it.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 max-h-[50vh] overflow-y-auto">
          {isLoading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-6 justify-center">
              <Loader2 size={12} className="animate-spin" /> Loading…
            </div>
          ) : !variables || variables.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-6">
              No variables yet. Add one below.
            </div>
          ) : (
            variables.map((v) => (
              <VariableRow
                key={v.id}
                variable={v}
                environment={environment}
                onReplace={() => setReplacing(v)}
              />
            ))
          )}
        </div>

        <DialogFooter>
          <Button onClick={() => setShowAdd(true)}>
            <Plus size={14} className="mr-1" /> Add variable
          </Button>
        </DialogFooter>
      </DialogContent>

      {showAdd && (
        <AddOrReplaceModal
          mode="add"
          environment={environment}
          onClose={() => setShowAdd(false)}
        />
      )}
      {replacing && (
        <AddOrReplaceModal
          mode="replace"
          environment={environment}
          existing={replacing}
          onClose={() => setReplacing(null)}
        />
      )}
    </Dialog>
  );
}

function VariableRow({
  variable,
  environment,
  onReplace,
}: {
  variable: VariableListItem;
  environment: string;
  onReplace: () => void;
}) {
  const del = useDeleteVariable(environment);
  const isSecret = variable.type === 'secret';

  return (
    <div className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-accent group">
      <span className="text-muted-foreground flex-shrink-0">
        {isSecret ? <Lock size={12} /> : <KeyRound size={12} />}
      </span>
      <span className="font-mono text-xs flex-shrink-0">{variable.key}</span>
      <span className="text-xs text-muted-foreground font-mono truncate flex-1">
        {isSecret ? '••••••••' : variable.value ?? ''}
      </span>
      <button
        onClick={onReplace}
        className="text-[10px] text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 transition-opacity"
        title="Replace value"
      >
        <Eye size={12} />
      </button>
      <button
        onClick={() => {
          if (!confirm(`Delete ${variable.key} from ${environment}?`)) return;
          del.mutate(variable.id, {
            onSuccess: () => toast.success(`Deleted ${variable.key}`),
            onError: (e) => toast.error(`Delete failed: ${(e as Error).message}`),
          });
        }}
        className="text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
        title="Delete"
        disabled={del.isPending}
      >
        <Trash2 size={12} />
      </button>
    </div>
  );
}

function AddOrReplaceModal({
  mode,
  environment,
  existing,
  onClose,
}: {
  mode: 'add' | 'replace';
  environment: string;
  existing?: VariableListItem;
  onClose: () => void;
}) {
  const [key, setKey] = useState(existing?.key ?? '');
  const [value, setValue] = useState('');
  const [type, setType] = useState<'string' | 'secret'>(
    (existing?.type as 'string' | 'secret') ?? 'secret',
  );
  const create = useCreateVariable();
  const update = useUpdateVariable(environment);

  const keyValid = mode === 'replace' || KEY_RE.test(key);
  const canSubmit = keyValid && value.length > 0 && !create.isPending && !update.isPending;

  const submit = () => {
    if (!canSubmit) return;
    if (mode === 'add') {
      create.mutate(
        { key, value, type, environment },
        {
          onSuccess: () => {
            toast.success(`Added ${key}`);
            onClose();
          },
          onError: (e) => toast.error(`Add failed: ${(e as Error).message}`),
        },
      );
    } else if (existing) {
      update.mutate(
        { id: existing.id, value },
        {
          onSuccess: () => {
            toast.success(`Replaced ${existing.key}`);
            onClose();
          },
          onError: (e) => toast.error(`Replace failed: ${(e as Error).message}`),
        },
      );
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{mode === 'add' ? 'Add variable' : `Replace ${existing?.key}`}</DialogTitle>
          <DialogDescription>
            {mode === 'add'
              ? `New variable in environment "${environment}".`
              : 'The new value is stored and immediately masked.'}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          {mode === 'add' && (
            <div>
              <label className="text-xs text-muted-foreground">Key</label>
              <Input
                value={key}
                onChange={(e) => setKey(e.target.value.toUpperCase())}
                placeholder="SLACK_TOKEN"
                className="font-mono"
                autoFocus
              />
              {key.length > 0 && !keyValid && (
                <p className="text-[10px] text-destructive mt-1">
                  Must match {String(KEY_RE)} (uppercase, digits, underscores)
                </p>
              )}
            </div>
          )}
          <div>
            <label className="text-xs text-muted-foreground">
              {type === 'secret' ? 'Value (hidden)' : 'Value'}
            </label>
            <Input
              type={type === 'secret' ? 'password' : 'text'}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoFocus={mode === 'replace'}
            />
          </div>
          {mode === 'add' && (
            <div>
              <label className="text-xs text-muted-foreground">Type</label>
              <div className="flex gap-2 mt-1">
                {(['string', 'secret'] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setType(t)}
                    className={`px-2 py-1 text-xs rounded-md border ${
                      type === t
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'border-border hover:bg-accent'
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {(create.isPending || update.isPending) && (
              <Loader2 size={12} className="mr-1 animate-spin" />
            )}
            {mode === 'add' ? 'Add' : 'Replace'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
