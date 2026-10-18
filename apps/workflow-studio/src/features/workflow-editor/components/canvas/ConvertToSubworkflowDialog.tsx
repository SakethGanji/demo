/**
 * "Convert to sub-workflow" (DESIGN-STAGE8-REUSE 12.4). The engine plans the move with a dry run on every change of
 * the selection or the name (debounced 300 ms), so a refusal (CS01 to CS10) shows before the user confirms. Confirm
 * saves the draft, creates the sub-workflow, and reloads the rewritten parent.
 */
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, ArrowUpRight, Loader2, Workflow } from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Badge } from '@/shared/components/ui/badge';
import { Field } from '@/shared/components/ui/field';
import { Callout } from '@/shared/components/ui/surface';
import { workflowsApi } from '@/shared/lib/api';
import { ApiError, errorMessage } from '@/shared/api/http';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorProjectStore } from '../../stores/editorProjectStore';
import { useConvertSubworkflowStore } from '../../stores/convertSubworkflowStore';
import { buildSavePayload, updateWorkflowDraft } from '../../lib/nodeActions';
import { flushPendingEdits } from '../../lib/pendingEdits';
import { fromBackendWorkflow } from '../../lib/workflowTransform';
import {
  refusalMessage,
  subworkflowsApi,
  type ExtractInput,
  type ExtractPlan,
} from '../../lib/subworkflowsApi';
import { undoConversion } from '../../lib/convertUndo';
import { useCommitExtract } from '../../hooks/useSubworkflows';

interface Refusal {
  code: string;
  message: string;
  nodes: string[];
}

type Preview =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ok'; plan: ExtractPlan; warnings: { code?: string; message: string }[] }
  | { state: 'refused'; refusal: Refusal }
  | { state: 'error'; message: string };

function originText(o: ExtractInput['origin']): string {
  return o === 'item' ? 'from the incoming item' : `from ${o.node}.${o.path}`;
}

function exitText(plan: ExtractPlan): string {
  const e = plan.exit;
  if (!e?.node) return 'Whichever end step runs last';
  return e.port && e.port !== 'main' ? `${e.node} (${e.port} output)` : e.node;
}

/** Reloads the saved draft into the editor (the store keeps no history across it). */
async function reloadDraft(workflowId: string) {
  const fresh = await workflowsApi.get(workflowId);
  useWorkflowStore.getState().loadWorkflow(fromBackendWorkflow(fresh));
}

function openChild(slug: string, id: string) {
  window.open(`/p/${slug}/workflows/${id}`, '_blank', 'noopener');
}

export function ConvertToSubworkflowDialog() {
  const open = useConvertSubworkflowStore((s) => s.open);
  const nodeNames = useConvertSubworkflowStore((s) => s.nodeNames);
  const close = useConvertSubworkflowStore((s) => s.close);
  const setHighlight = useConvertSubworkflowStore((s) => s.setHighlight);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const slug = useEditorProjectStore((s) => s.slug);
  const commit = useCommitExtract();

  const [name, setName] = useState('');
  const [preview, setPreview] = useState<Preview>({ state: 'idle' });
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const seq = useRef(0);

  // a fresh dialog starts with a suggested name
  useEffect(() => {
    if (open) {
      setName('New sub-workflow');
      setChecking(false);
    }
  }, [open]);

  // dry run, debounced 300 ms
  useEffect(() => {
    if (!open) return;
    if (!workflowId) {
      setChecking(false);
      setPreview({ state: 'error', message: 'Save this workflow first, then convert part of it.' });
      return;
    }
    const trimmed = name.trim();
    if (!trimmed) {
      setChecking(false);
      setPreview({ state: 'idle' });
      return;
    }
    const mine = ++seq.current;
    setChecking(true);
    setPreview((p) => (p.state === 'ok' ? p : { state: 'loading' }));
    const timer = setTimeout(() => {
      flushPendingEdits();
      const definition = buildSavePayload(workflowId) as unknown as Record<string, unknown>;
      subworkflowsApi
        .previewExtract(workflowId, nodeNames, trimmed, definition)
        .then((res) => {
          if (mine !== seq.current) return;
          setChecking(false);
          setHighlight([]);
          setPreview({ state: 'ok', plan: res.plan, warnings: res.warnings ?? [] });
        })
        .catch((e: unknown) => {
          if (mine !== seq.current) return;
          setChecking(false);
          if (e instanceof ApiError && e.status === 422 && e.code && /^CS\d+$/.test(e.code)) {
            const nodes = Array.isArray(e.details.nodes) ? (e.details.nodes as string[]) : [];
            setHighlight(nodes);
            setPreview({ state: 'refused', refusal: { code: e.code, message: refusalMessage(e.code, e.message), nodes } });
          } else {
            setHighlight([]);
            setPreview({ state: 'error', message: errorMessage(e) });
          }
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [open, workflowId, nodeNames, name, setHighlight]);

  const onOpenChange = (o: boolean) => {
    if (!o && !saving) {
      seq.current++;
      close();
    }
  };

  const confirm = async () => {
    if (!workflowId || preview.state !== 'ok') return;
    setSaving(true);
    try {
      flushPendingEdits();
      // what the parent looked like before: "Delete it" in the toast puts this back
      const before = buildSavePayload(workflowId);
      await updateWorkflowDraft(workflowId);
      const res = await commit.mutateAsync({ workflowId, nodes: nodeNames, name: name.trim() });
      const childId = res.child_workflow_id;
      await reloadDraft(workflowId);
      seq.current++;
      close();
      if (childId) showResultToast(childId, name.trim(), res.child_version_number ?? null, before, workflowId, slug);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.code && /^CS\d+$/.test(e.code)) {
        setPreview({ state: 'refused', refusal: { code: e.code, message: refusalMessage(e.code, e.message), nodes: [] } });
      } else {
        toast.error('Could not convert the selection', { description: errorMessage(e) });
      }
    } finally {
      setSaving(false);
    }
  };

  const plan = preview.state === 'ok' ? preview.plan : null;
  const warnings = preview.state === 'ok' ? preview.warnings : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" data-testid="convert-subwf-dialog">
        <DialogHeader>
          <DialogTitle>Convert to sub-workflow</DialogTitle>
          <DialogDescription>
            Moves {nodeNames.length} selected step{nodeNames.length === 1 ? '' : 's'} into their own workflow and calls it from here.
          </DialogDescription>
        </DialogHeader>

        <Field label="Name" htmlFor="convert-subwf-name">
          <Input
            id="convert-subwf-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            data-testid="convert-subwf-name"
          />
        </Field>

        <div className="min-h-24 space-y-3" aria-live="polite">
          {(preview.state === 'loading' || (checking && preview.state === 'ok')) && (
            <p className="flex items-center gap-2 text-[12.5px] text-muted-foreground" data-testid="convert-subwf-loading">
              <Loader2 size={13} className="animate-spin" aria-hidden /> Checking the selection...
            </p>
          )}

          {preview.state === 'refused' && (
            <Callout tone="error" icon={<AlertTriangle />} data-testid="convert-subwf-refusal" data-code={preview.refusal.code}>
              <p className="text-[12.5px]">
                <span className="mr-1.5 font-mono font-medium">{preview.refusal.code}</span>
                {preview.refusal.message}
              </p>
              {preview.refusal.nodes.length > 0 && (
                <p className="mt-0.5 text-[12px]">Highlighted on the canvas: {preview.refusal.nodes.join(', ')}</p>
              )}
            </Callout>
          )}

          {preview.state === 'error' && (
            <Callout tone="error" icon={<AlertTriangle />} data-testid="convert-subwf-error">
              <p className="text-[12.5px]">{preview.message}</p>
            </Callout>
          )}

          {plan && (
            <>
              <div className="space-y-1" data-testid="convert-subwf-inputs">
                <span className="text-[12.5px] font-medium text-foreground">Inputs it will take</span>
                {plan.inputs.length === 0 ? (
                  <p className="text-[12px] text-muted-foreground">None. It works on the incoming item.</p>
                ) : (
                  <ul className="space-y-1">
                    {plan.inputs.map((f) => (
                      <li key={f.name} className="flex items-center gap-1.5 text-[12.5px]" data-testid={`convert-subwf-input-${f.name}`}>
                        <span className="font-mono">{f.name}</span>
                        <Badge variant="neutral">{f.type}</Badge>
                        {f.required && <Badge variant="primary">required</Badge>}
                        <span className="truncate text-muted-foreground">{originText(f.origin)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="space-y-0.5" data-testid="convert-subwf-exit">
                <span className="text-[12.5px] font-medium text-foreground">Returns the output of</span>
                <p className="text-[12.5px] text-muted-foreground">{exitText(plan)}</p>
              </div>

              {warnings.length > 0 && (
                <Callout tone="warn" icon={<AlertTriangle />} data-testid="convert-subwf-warnings">
                  <ul className="space-y-0.5 text-[12px]">
                    {warnings.map((w, i) => (
                      <li key={i}>{w.message}</li>
                    ))}
                  </ul>
                </Callout>
              )}

              {plan.affected_tests.length > 0 && (
                <Callout tone="warn" icon={<AlertTriangle />} data-testid="convert-subwf-affected-tests">
                  <p className="text-[12px]">
                    {plan.affected_tests.length} test{plan.affected_tests.length === 1 ? '' : 's'} will need a review, because the steps they check move:{' '}
                    {plan.affected_tests.map((t) => t.name || t.id).join(', ')}.
                  </p>
                </Callout>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving} data-testid="convert-subwf-cancel">
            Cancel
          </Button>
          <Button onClick={() => void confirm()} disabled={preview.state !== 'ok' || checking || saving || !name.trim()} data-testid="convert-subwf-confirm">
            {saving ? <Loader2 className="animate-spin" /> : <Workflow />} Convert
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Result toast: open the new sub-workflow, or put the parent back and delete it. */
function showResultToast(
  childId: string,
  childName: string,
  version: number | null,
  parentBefore: ReturnType<typeof buildSavePayload>,
  parentId: string,
  slug: string,
) {
  const undo = async (resultId: string | number | undefined, parentAlreadyRestored = false) => {
    // the result toast is a custom jsx payload that survives an in-place update: drop it, then use a fresh id
    if (resultId !== undefined) toast.dismiss(resultId);
    const t = toast.loading(parentAlreadyRestored ? 'Deleting the sub-workflow…' : 'Putting the steps back…', { id: `convert-undo-${childId}-${Date.now()}` });
    const out = await undoConversion({
      restoreParent: async () => {
        if (parentAlreadyRestored) return;
        await workflowsApi.update(parentId, parentBefore);
        await reloadDraft(parentId);
      },
      deleteChild: async () => {
        await workflowsApi.delete(childId);
      },
    });
    if (out.kind === 'done') {
      toast.success('Put the steps back and deleted the sub-workflow', { id: t });
    } else if (out.kind === 'orphaned') {
      // the steps are back, but the sub-workflow is still there and nothing calls it
      toast.warning(`Steps are back, but "${childName}" still exists`, {
        id: t,
        description: `Nothing calls it now. ${errorMessage(out.error)}`,
        action: { label: 'Delete it', onClick: () => void undo(t, true) },
        duration: 20000,
      });
    } else {
      toast.error('Could not undo the conversion', { id: t, description: errorMessage(out.error) });
    }
  };

  toast.custom(
    (id) => (
      <div className="glass-overlay pointer-events-auto flex w-80 flex-col gap-2 px-3.5 py-3 text-[13px]" data-testid="convert-subwf-toast">
        <div>
          <p className="font-medium">Created "{childName}"</p>
          <p className="text-[12.5px] text-muted-foreground">
            {version != null ? `Published as v${version}. ` : 'Left as a draft: publish it before you publish this workflow. '}
            This workflow now calls it.
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Button size="xs" variant="outline" onClick={() => openChild(slug, childId)} data-testid="convert-subwf-open">
            Open sub-workflow <ArrowUpRight />
          </Button>
          <Button size="xs" variant="ghost" data-testid="convert-subwf-delete" onClick={() => void undo(id)}>
            Delete it
          </Button>
        </div>
      </div>
    ),
    { duration: 20000 },
  );
}
