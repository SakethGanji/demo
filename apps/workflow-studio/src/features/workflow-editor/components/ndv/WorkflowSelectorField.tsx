import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
/**
 * WorkflowSelectorField - Dropdown for selecting a workflow by ID
 *
 * Used by the Execute Workflow node to select which workflow to execute.
 */

import { useId } from 'react';
import { useWorkflows } from '@/features/projects/hooks/useWorkflows';

interface NodeProperty {
  displayName: string;
  name: string;
  type: string;
  default?: unknown;
  required?: boolean;
  placeholder?: string;
  description?: string;
}

interface WorkflowSelectorFieldProps {
  property: NodeProperty;
  value: string | undefined;
  onChange: (value: string) => void;
  /** Current workflow ID to exclude from the list (prevent self-reference) */
  currentWorkflowId?: string;
}

export function WorkflowSelectorField({
  property,
  value,
  onChange,
  currentWorkflowId,
}: WorkflowSelectorFieldProps) {
  const fieldId = useId();
  const { data: workflows, isLoading, error } = useWorkflows();

  // Filter out the current workflow to prevent self-referencing
  const availableWorkflows = workflows?.filter(
    (wf) => wf.id !== currentWorkflowId
  );

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-[12.5px] font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-0.5">*</span>}
      </label>
      <Select
        value={value || ''}
        onValueChange={(v) => onChange((v as string) || '')}
        disabled={isLoading}
        items={{
          '': isLoading ? 'Loading workflows...' : error ? 'Error loading workflows' : 'Select a workflow...',
          ...Object.fromEntries((availableWorkflows ?? []).map((w) => [w.id, `${w.name}${!w.active ? ' (Inactive)' : ''}`])),
        }}
      >
        <SelectTrigger id={fieldId} className="w-full" data-testid="workflow-selector">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {availableWorkflows?.map((workflow) => (
            <SelectItem key={workflow.id} value={workflow.id}>
              {workflow.name}
              {!workflow.active && ' (Inactive)'}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {property.description && (
        <p className="mt-1 text-[12px] leading-4 text-muted-foreground">{property.description}</p>
      )}
      {error && (
        <p className="mt-1 text-[12px] text-destructive">
          Failed to load workflows. Please try again.
        </p>
      )}
    </div>
  );
}
