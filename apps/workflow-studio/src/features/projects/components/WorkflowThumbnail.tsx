import { type CSSProperties, useMemo } from 'react';

import type { WorkflowDefinition } from '../hooks/useWorkflows';
import { definitionToPreviewData } from '@/features/workflow-editor/lib/workflowTransform';
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG';

interface WorkflowThumbnailProps {
  definition: WorkflowDefinition;
  className?: string;
  style?: CSSProperties;
}

export function WorkflowThumbnail({ definition, className, style }: WorkflowThumbnailProps) {
  const { nodes, edges } = useMemo(() => definitionToPreviewData(definition), [definition]);
  return <WorkflowSVG nodes={nodes} edges={edges} showDotGrid className={className} style={style} />;
}
