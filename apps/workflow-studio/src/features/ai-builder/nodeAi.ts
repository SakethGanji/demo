/**
 * Pure helpers for "AI on any node" (DESIGN-STAGE6-AI-BUILDER section 7): what each task is called, which nodes it applies to,
 * what must be filled in, and how the engine's two kinds of answer (an explanation, or a proposal to review) are told apart.
 */
import type { NodeAiExplain, NodeAiTask, Proposal } from './types';

export interface NodeAiTaskInfo {
  task: NodeAiTask;
  label: string;
  /** one line under the label, in plain English */
  hint: string;
  testId: string;
}

export const NODE_AI_TASKS: NodeAiTaskInfo[] = [
  { task: 'fix_error', label: 'Fix this error', hint: 'Look at the last failed run of this step and repair it', testId: 'node-ai-fix-error' },
  { task: 'write_code', label: 'Write the code', hint: 'Write or change the code in this step', testId: 'node-ai-write-code' },
  { task: 'write_expression', label: 'Write an expression', hint: 'Fill one setting with a value worked out from the data', testId: 'node-ai-write-expression' },
  { task: 'explain', label: 'Explain this step', hint: 'Say in plain words what this step does', testId: 'node-ai-explain' },
];

/** Node types whose body is code the AI can write. */
export const CODE_NODE_TYPES = ['Code', 'PythonStep'] as const;

export const MAX_INSTRUCTION_CHARS = 2000;

export function taskInfo(task: NodeAiTask): NodeAiTaskInfo {
  return NODE_AI_TASKS.find((t) => t.task === task) ?? NODE_AI_TASKS[3];
}

/** Can this task run on this kind of node? `reason` says why not, in plain English. */
export function taskAvailability(task: NodeAiTask, nodeType: string): { ok: boolean; reason?: string } {
  if (task === 'write_code' && !(CODE_NODE_TYPES as readonly string[]).includes(nodeType)) {
    return { ok: false, reason: 'Only code steps have code to write.' };
  }
  return { ok: true };
}

/** Which inputs the dialog asks for. */
export function formShape(task: NodeAiTask): { field: boolean; instruction: 'required' | 'optional' } {
  switch (task) {
    case 'write_expression': return { field: true, instruction: 'required' };
    case 'write_code': return { field: false, instruction: 'required' };
    case 'fix_error': return { field: false, instruction: 'optional' };
    default: return { field: false, instruction: 'optional' };
  }
}

/** A plain-English problem with the form, or null when it can be sent. */
export function validateNodeAiForm(task: NodeAiTask, v: { field: string; instruction: string }): string | null {
  const shape = formShape(task);
  if (shape.field && !v.field.trim()) return 'Say which setting to fill, for example body.amount.';
  if (shape.instruction === 'required' && !v.instruction.trim()) {
    return task === 'write_code' ? 'Describe what the code should do.' : 'Describe what the value should be.';
  }
  if (v.instruction.length > MAX_INSTRUCTION_CHARS) return `Please keep it under ${MAX_INSTRUCTION_CHARS.toLocaleString('en-US')} characters.`;
  return null;
}

export function dialogTitle(task: NodeAiTask, nodeLabel: string): string {
  switch (task) {
    case 'fix_error': return `Fix the error in "${nodeLabel}"`;
    case 'write_code': return `Write the code for "${nodeLabel}"`;
    case 'write_expression': return `Write an expression for "${nodeLabel}"`;
    default: return `Explain "${nodeLabel}"`;
  }
}

/** The engine answers `explain` with `{answer, proposal_id}` and everything else with a proposal. */
export function isExplainAnswer(r: NodeAiExplain | Proposal): r is NodeAiExplain {
  return 'proposal_id' in r && typeof (r as NodeAiExplain).answer === 'string';
}

/** The body of the request for one task. */
export function nodeAssistArgs(task: NodeAiTask, actor: string, v: { field: string; instruction: string }) {
  const instruction = v.instruction.trim();
  return {
    task, actor,
    ...(task === 'write_expression' ? { field: v.field.trim() } : {}),
    ...(instruction ? { instruction } : {}),
  };
}
