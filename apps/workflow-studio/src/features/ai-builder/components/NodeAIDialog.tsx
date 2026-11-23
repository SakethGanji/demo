import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useReactFlow } from '@xyflow/react';
import { toast } from 'sonner';
import { Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog';
import { Field } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet';
import { Textarea } from '@/shared/components/ui/textarea';
import { useHistoryStore } from '@/features/workflow-editor/stores/historyStore';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';
import { useEditorProjectStore } from '@/features/workflow-editor/stores/editorProjectStore';
import { useSaveWorkflow } from '@/features/workflow-editor/hooks/useWorkflowApi';
import { flushPendingEdits } from '@/features/workflow-editor/lib/pendingEdits';
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes';
import { MAX_INSTRUCTION_CHARS, dialogTitle, formShape, taskInfo } from '../nodeAi';
import { discardNodeProposal, keepNodeProposal, submitNodeAssist, undoNodeProposal, useNodeAiStore } from '../nodeAiStore';
import { ghostNodesFor, marksFromDiff } from '../proposalMarks';
import { refreshAfterWorkflowChange } from '../useAiBuilder';
import { useActorName } from '../useActorName';
import { ActorPrompt } from './ActorPrompt';
import { AiError } from './AiError';
import { ReviewPanel } from './ReviewPanel';

const INSTRUCTION_HELP: Record<string, string> = {
  fix_error: 'Optional. Say what you expected to happen, or leave this empty to fix the last failed run.',
  write_code: 'Say what the code should do with the data this step receives.',
  write_expression: 'Say what the value should be, in your own words.',
  explain: 'Optional. Ask a particular question, or leave this empty for a general explanation.',
};

/**
 * The request for one node task, and the answer. An explanation is shown here; anything that would change the workflow is shown as
 * a proposal in a side sheet with the changed steps marked on the canvas, and nothing is saved until the person keeps it.
 */
export default function NodeAIDialog() {
  const target = useNodeAiStore((s) => s.target);
  const phase = useNodeAiStore((s) => s.phase);
  const field = useNodeAiStore((s) => s.field);
  const instruction = useNodeAiStore((s) => s.instruction);
  const answer = useNodeAiStore((s) => s.answer);
  const proposal = useNodeAiStore((s) => s.proposal);
  const busy = useNodeAiStore((s) => s.busy);
  const error = useNodeAiStore((s) => s.error);
  const focusNode = useNodeAiStore((s) => s.focusNode);
  const actor = useActorName();
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const slug = useEditorProjectStore((s) => s.slug);
  const { saveWorkflow } = useSaveWorkflow();
  const qc = useQueryClient();
  const { fitView } = useReactFlow();
  const setDiff = useHistoryStore((s) => s.setDiff);
  const clearDiff = useHistoryStore((s) => s.clearDiff);
  const { setField, setInstruction, setFocusNode, close } = useNodeAiStore.getState();

  const reviewing = phase === 'review' && !!proposal;

  // mark what the proposal changes on the editor's own canvas (added green, changed amber, removed ghosts)
  useEffect(() => {
    if (!reviewing || !proposal) return;
    const base = (proposal.base_definition ?? null) as ApiWorkflowDetail['definition'] | null;
    setDiff(marksFromDiff(proposal.diff), ghostNodesFor(proposal.diff?.nodes.removed ?? [], base));
    return () => clearDiff();
  }, [reviewing, proposal, setDiff, clearDiff]);

  // bring the step chosen in the review into view
  useEffect(() => {
    if (focusNode) void fitView({ nodes: [{ id: focusNode }], padding: 0.6, maxZoom: 1, duration: 250 });
  }, [focusNode, fitView]);

  if (!target) return null;
  const info = taskInfo(target.task);
  const shape = formShape(target.task);
  const working = phase === 'working' || busy === 'drafting';

  // The AI starts from the saved draft, so what is on the canvas is saved first.
  const beforeRequest = async (): Promise<boolean> => {
    flushPendingEdits();
    if (!useWorkflowStore.getState().isDirty()) return true;
    try {
      await saveWorkflow();
      return true;
    } catch {
      return false; // saveWorkflow already told the person
    }
  };

  const send = () => { if (workflowId && !working) void submitNodeAssist(workflowId, beforeRequest); };

  const keep = async (opts: { acceptFailing?: boolean }) => {
    const r = await keepNodeProposal(opts);
    if (!r) return;
    refreshAfterWorkflowChange(qc, r.workflowId);
    toast.success('Change kept', {
      description: 'The step has been updated.',
      action: { label: 'Undo', onClick: () => void undoNodeProposal(r.proposal.id).then((p) => { if (p) toast.success('Change undone'); }) },
      duration: 12_000,
    });
  };

  const undo = async () => {
    const p = await undoNodeProposal();
    if (p) refreshAfterWorkflowChange(qc, p.workflow_id);
  };

  return (
    <>
      <Dialog open={!reviewing} onOpenChange={(o) => { if (!o) close(); }}>
        <DialogContent className="sm:max-w-lg" data-testid="node-ai-dialog" data-phase={phase}>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><Sparkles className="size-4 text-muted-foreground" /> {dialogTitle(target.task, target.nodeLabel)}</DialogTitle>
            <DialogDescription>{info.hint}. {target.task === 'explain' ? 'Nothing is changed.' : 'You will see the change and its tests before anything is saved.'}</DialogDescription>
          </DialogHeader>

          {!actor ? (
            <ActorPrompt />
          ) : (
            <div className="flex flex-col gap-3">
              <AiError error={error} />
              {phase === 'answer' ? (
                <div className="whitespace-pre-wrap rounded-ctl bg-recess p-3 text-[13px] leading-5" data-testid="node-ai-answer">{answer}</div>
              ) : (
                <>
                  {shape.field && (
                    <Field label="Which setting?" htmlFor="node-ai-field" help="The name of the setting to fill, for example body.amount or fields[0].value.">
                      <Input id="node-ai-field" value={field} disabled={working} onChange={(e) => setField(e.target.value)} placeholder="body.amount" data-testid="node-ai-field" />
                    </Field>
                  )}
                  <Field
                    label={shape.instruction === 'required' ? 'What do you need?' : 'Anything to add?'}
                    htmlFor="node-ai-instruction"
                    help={INSTRUCTION_HELP[target.task]}
                  >
                    <Textarea
                      id="node-ai-instruction" rows={4} value={instruction} disabled={working} maxLength={MAX_INSTRUCTION_CHARS + 200}
                      onChange={(e) => setInstruction(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(); }}
                      data-testid="node-ai-instruction"
                    />
                  </Field>
                </>
              )}
            </div>
          )}

          {actor && (
            <DialogFooter>
              {phase === 'answer' ? (
                <>
                  <Button variant="outline" onClick={() => useNodeAiStore.setState({ phase: 'ask', answer: null })}>Ask something else</Button>
                  <Button onClick={close}>Done</Button>
                </>
              ) : (
                <>
                  <Button variant="outline" onClick={close} disabled={working}>Cancel</Button>
                  <Button onClick={send} disabled={working || !workflowId} data-testid="node-ai-submit">
                    {working ? <Loader2 className="animate-spin" /> : <Sparkles />}
                    {working ? 'Working…' : target.task === 'explain' ? 'Explain' : 'Ask the AI'}
                  </Button>
                </>
              )}
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      <Sheet open={reviewing} onOpenChange={(o) => { if (!o) close(); }}>
        <SheetContent side="right" showCloseButton overlayClassName="hidden" className="sm:max-w-md" data-testid="node-ai-review">
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pb-4 pt-4">
            <SheetTitle className="mb-1 text-[15px] font-semibold tracking-[-0.01em]">{dialogTitle(target.task, target.nodeLabel)}</SheetTitle>
            <SheetDescription className="mb-3 text-[12.5px] text-muted-foreground">Review the change. Nothing is saved until you keep it.</SheetDescription>
            <AiError error={error} className="mb-3" />
            {proposal && (
              <ReviewPanel
                key={proposal.id}
                proposal={proposal}
                projectSlug={slug}
                busy={busy}
                selectedNode={focusNode}
                onSelectNode={(n) => { useWorkflowStore.getState().setSelectedNode(n); setFocusNode(n); }}
                onKeep={(o) => void keep(o)}
                onDiscard={() => void discardNodeProposal().then((p) => { if (p) close(); })}
                onRevise={() => undefined}
                onUndo={() => void undo()}
                allowRevise={false}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
