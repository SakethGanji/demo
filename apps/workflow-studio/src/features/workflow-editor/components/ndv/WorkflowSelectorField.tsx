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
      <label htmlFor={fieldId} className="mb-1 block text-xs font-medium text-foreground/80">
        {property.displayName}
        {property.required && <span className="text-destructive ml-0.5">*</span>}
      </label>
      <select
        id={fieldId}
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        disabled={isLoading}
        className="w-full rounded border border-border/60 bg-background px-2.5 py-1.5 text-[13px] focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring/50 disabled:opacity-50"
      >
        <option value="">
          {isLoading
            ? 'Loading workflows...'
            : error
              ? 'Error loading workflows'
              : 'Select a workflow...'}
        </option>
        {availableWorkflows?.map((workflow) => (
          <option key={workflow.id} value={workflow.id}>
            {workflow.name}
            {!workflow.active && ' (Inactive)'}
          </option>
        ))}
      </select>
      {property.description && (
        <p className="mt-1 text-[11px] text-muted-foreground/70">{property.description}</p>
      )}
      {error && (
        <p className="mt-1 text-xs text-destructive">
          Failed to load workflows. Please try again.
        </p>
      )}
    </div>
  );
}
