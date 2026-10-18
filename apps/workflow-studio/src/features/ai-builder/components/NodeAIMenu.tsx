import { Sparkles } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { NODE_AI_TASKS, taskAvailability } from '../nodeAi';
import { useNodeAiStore } from '../nodeAiStore';

/**
 * "AI" on a node: fix its error, write its code, write one of its expressions, or explain it. Choosing a task opens the request dialog;
 * anything that would change the workflow comes back as a proposal to review first.
 */
export function NodeAIMenu({ nodeName, nodeLabel, nodeType, disabled, onChoose }: {
  nodeName: string;
  nodeLabel: string;
  nodeType: string;
  /** e.g. the workflow is not saved yet */
  disabled?: boolean;
  /** called after a task is chosen (the node panel closes itself here) */
  onChoose?: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button size="sm" variant="outline" disabled={disabled} data-testid="node-ai-menu" title={disabled ? 'Save the workflow first' : 'Ask the AI about this step'} />}
      >
        <Sparkles />
        <span className="hidden sm:inline">AI</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Ask the AI about this step</DropdownMenuLabel>
          {NODE_AI_TASKS.map((t) => {
            const a = taskAvailability(t.task, nodeType);
            return (
              <DropdownMenuItem
                key={t.task}
                disabled={!a.ok}
                data-testid={t.testId}
                className="flex flex-col items-start gap-0"
                onClick={() => {
                  useNodeAiStore.getState().open({ nodeName, nodeLabel, nodeType, task: t.task });
                  onChoose?.();
                }}
              >
                <span>{t.label}</span>
                <span className="text-[12px] text-muted-foreground">{a.ok ? t.hint : a.reason}</span>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
