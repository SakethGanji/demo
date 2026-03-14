import { memo, useState, useMemo, useRef, useEffect } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { Plus, Check, X } from 'lucide-react';
import { useEditorLayoutStore } from '../../../stores/editorLayoutStore';
import { useNDVStore } from '../../../stores/ndvStore';
import { useWorkflowStore } from '../../../stores/workflowStore';
import type { WorkflowNodeData } from '../../../types/workflow';
import {
  getNodeStyles,
  getNodeShapeConfig,
  calculateHandlePositions,
  calculateNodeDimensions,
} from '../../../lib/nodeStyles';
import { normalizeNodeGroup } from '../../../lib/nodeConfig';
import { getIconForNode } from '../../../lib/nodeIcons';
import { isTriggerType } from '../../../lib/nodeConfig';
import {
  useNodeExecution,
  useHasInputConnection,
  useConnectedOutputHandles,
} from '../../../hooks/useWorkflowSelectors';


// Status badge component for success/error states
const StatusBadge = ({ status }: { status: 'success' | 'error' }) => {
  const isSuccess = status === 'success';
  return (
    <div
      className={`
        absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full
        flex items-center justify-center text-primary-foreground
        shadow-sm animate-badge-pop z-10
        ${isSuccess ? 'bg-[var(--success)]' : 'bg-destructive'}
      `}
    >
      {isSuccess ? <Check size={10} strokeWidth={3} /> : <X size={10} strokeWidth={3} />}
    </div>
  );
};


function WorkflowNode({ id, data, selected }: NodeProps<Node<WorkflowNodeData>>) {
  const [isHovered, setIsHovered] = useState(false);
  const [isEditingLabel, setIsEditingLabel] = useState(false);
  const labelInputRef = useRef<HTMLInputElement>(null);

  const openForConnection = useEditorLayoutStore((s) => s.openForConnection);
  const openNDV = useNDVStore((s) => s.openNDV);
  const executionData = useNodeExecution(id);
  const isDragging = useWorkflowStore((s) => s.draggedNodeType !== null);
  const isExecActive = useWorkflowStore((s) => s.isAnyNodeRunning);
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);

  const hasInputConnection = useHasInputConnection(id);
  const connectedOutputHandles = useConnectedOutputHandles(id);

  // Focus input when editing starts
  useEffect(() => {
    if (isEditingLabel && labelInputRef.current) {
      labelInputRef.current.focus();
      labelInputRef.current.select();
    }
  }, [isEditingLabel]);

  const handleLabelDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsEditingLabel(true);
  };

  const handleLabelSave = () => {
    const inputValue = labelInputRef.current?.value || '';
    const trimmedLabel = inputValue.trim();
    if (trimmedLabel && trimmedLabel !== data.label) {
      updateNodeData(id, { label: trimmedLabel });
    }
    setIsEditingLabel(false);
  };

  const handleLabelKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleLabelSave();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setIsEditingLabel(false);
    }
  };

  const IconComponent = useMemo(
    () => getIconForNode(data.icon, data.type),
    [data.icon, data.type]
  );
  // Use centralized trigger detection — dynamic via nodeTypesMap when available
  const nodeTypesMap = useWorkflowStore((s) => s.nodeTypesMap);
  const isTrigger = isTriggerType(data.type || '', nodeTypesMap);

  // Get group-based styling and shape
  const nodeGroup = useMemo(
    () => normalizeNodeGroup(data.group ? [data.group] : undefined),
    [data.type, data.group]
  );
  const styles = useMemo(() => getNodeStyles(nodeGroup), [nodeGroup]);
  const shapeConfig = useMemo(() => getNodeShapeConfig(nodeGroup), [nodeGroup]);

  // Calculate input/output counts (ensure at least 1 input for non-triggers, at least 1 output)
  const inputCount = isTrigger ? 0 : Math.max(1, data.inputCount ?? data.inputs?.length ?? 1);
  const outputCount = Math.max(1, data.outputCount ?? data.outputs?.length ?? 1);

  // Calculate dimensions and handle positions (proportional sizing)
  const dimensions = useMemo(
    () => calculateNodeDimensions(inputCount, outputCount),
    [inputCount, outputCount]
  );
  const inputPositions = useMemo(
    () => calculateHandlePositions(inputCount),
    [inputCount]
  );
  const outputPositions = useMemo(
    () => calculateHandlePositions(outputCount),
    [outputCount]
  );

  // Show actions when hovered OR selected
  const showActions = isHovered || selected;

  const handleAddNode = (e: React.MouseEvent, handleId: string) => {
    e.stopPropagation();
    openForConnection(id, handleId);
  };

  // Execution status flags
  const isRunning = executionData?.status === 'running';
  const isSuccess = executionData?.status === 'success';
  const isError = executionData?.status === 'error';
  const isIdle = isExecActive && (!executionData || executionData.status === 'idle');

  // Check if this node can be a drop target (has unconnected input and something is being dragged)
  const canBeDropTarget = useMemo(() => {
    if (!isDragging) return false;
    if (isTrigger) return false; // Triggers have no inputs
    // Check if this node already has an input connection
    return !hasInputConnection;
  }, [isDragging, isTrigger, hasInputConnection]);

  // Render input handles (labels shown on edges if needed)
  const renderInputHandles = () => {
    if (isTrigger || inputCount === 0) return null;

    return inputPositions.map((position, index) => {
      const inputDef = data.inputs?.[index];

      return (
        <Handle
          key={`input-${index}`}
          type="target"
          position={Position.Left}
          id={inputDef?.name || `input-${index}`}
          style={{
            top: `${position}%`,
            backgroundColor: styles.handleColor,
            borderColor: styles.handleColor,
          }}
          className="!h-1.5 !w-1.5 !border-2"
        />
      );
    });
  };

  // Render output handles - transforms into plus button on hover when not connected
  const renderOutputHandles = () => {
    return outputPositions.map((position, index) => {
      const outputDef = data.outputs?.[index];
      const handleId = outputDef?.name || `output-${index}`;
      const hasConnection = connectedOutputHandles.has(handleId);
      const canExpand = !hasConnection && showActions;

      return (
        <div
          key={`output-wrapper-${index}`}
          className="absolute"
          style={{
            right: 0,
            top: `${position}%`,
            transform: 'translate(50%, -50%)',
          }}
        >
          {/* Clickable wrapper for the plus action - only active when expanded */}
          <div
            onClick={(e) => {
              if (canExpand) {
                handleAddNode(e, handleId);
              }
            }}
            className={`
              nodrag relative flex items-center justify-center
              transition-all duration-200 ease-out
              ${canExpand
                ? 'h-5 w-5 cursor-pointer hover:scale-110'
                : 'h-1.5 w-1.5'
              }
            `}
            style={{ pointerEvents: canExpand ? 'all' : 'none' }}
          >
            {/* The actual ReactFlow handle - invisible but functional for dragging */}
            <Handle
              type="source"
              position={Position.Right}
              id={handleId}
              className="!absolute !inset-0 !h-full !w-full !transform-none !border-0 !bg-transparent"
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
              }}
            />
            {/* Visual representation - dot or plus button */}
            <div
              className={`
                pointer-events-none flex items-center justify-center
                rounded-full transition-all duration-200 ease-out
                ${canExpand
                  ? 'h-5 w-5 border border-border bg-card shadow-sm hover:bg-accent hover:shadow-md'
                  : 'h-1.5 w-1.5 border-2'
                }
              `}
              style={{
                backgroundColor: canExpand ? undefined : styles.handleColor,
                borderColor: canExpand ? undefined : styles.handleColor,
              }}
            >
              {canExpand && (
                <Plus size={12} className="text-muted-foreground" />
              )}
            </div>
          </div>
        </div>
      );
    });
  };

  return (
    <div
      className={`relative flex flex-col items-center ${isIdle ? 'node-exec-idle' : ''}`}
      style={{
        transition: 'opacity 0.4s ease',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Drop zone indicator - shows on the left when node can accept a connection */}
      {canBeDropTarget && (
        <div
          className="absolute -left-8 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full border-2 border-dashed border-[var(--success)] bg-[var(--success)]/20 animate-pulse flex items-center justify-center"
          style={{ pointerEvents: 'none' }}
        >
          <Plus size={12} className="text-[var(--success)]" />
        </div>
      )}

      <div
        className={`
          relative cursor-grab border transition-all duration-300 flex items-center justify-center
          ${selected ? 'ring-2 ring-offset-1' : ''}
          ${canBeDropTarget ? 'ring-2 ring-[var(--success)]/50 ring-offset-1' : ''}
        `}
        style={{
          height: dimensions.height,
          width: dimensions.width,
          backgroundColor: styles.bgColor,
          borderColor: canBeDropTarget ? 'var(--success)' : (isRunning ? styles.accentColor : (selected ? styles.accentColor : styles.borderColor)),
          borderWidth: (selected || isRunning) ? 2 : 1,
          borderRadius: shapeConfig.borderRadius,
          boxShadow: canBeDropTarget
            ? '0 0 15px var(--success)'
            : isHovered
              ? '0 6px 16px rgba(0,0,0,0.14), 0 2px 6px rgba(0,0,0,0.08)'
              : (selected ? `0 4px 14px ${styles.accentColor}30, 0 2px 4px rgba(0,0,0,0.1)` : '0 2px 6px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.06)'),
          // @ts-expect-error CSS custom property
          '--tw-ring-color': canBeDropTarget ? 'var(--success)' : styles.accentColor,
        }}
      >
        {/* Execution ring overlays — clean box-shadow animations, no blur */}
        {isRunning && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              borderRadius: shapeConfig.borderRadius,
              // @ts-expect-error CSS custom property
              '--ring-color': styles.accentColor,
              animation: 'node-running-ring 2s ease-in-out infinite',
            }}
          />
        )}
        {isSuccess && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              borderRadius: shapeConfig.borderRadius,
              animation: 'node-success-ring 0.6s ease-out forwards',
            }}
          />
        )}
        {isError && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              borderRadius: shapeConfig.borderRadius,
              animation: 'node-error-ring 0.5s ease-out forwards',
            }}
          />
        )}

        {/* Input Handles */}
        {renderInputHandles()}

        {/* Node Content - Icon only, centered */}
        <div
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors duration-300"
          style={{
            backgroundColor: styles.iconBgColor,
            color: styles.iconFgColor,
          }}
        >
          <IconComponent size={20} />
        </div>

        {/* Status badges for success/error */}
        {isSuccess && <StatusBadge status="success" />}
        {isError && <StatusBadge status="error" />}

        {/* Output Handles */}
        {renderOutputHandles()}
      </div>

      {/* Node Label - Below the node (double-click to edit) */}
      {isEditingLabel ? (
        <input
          ref={labelInputRef}
          type="text"
          defaultValue={data.label}
          onBlur={handleLabelSave}
          onKeyDown={handleLabelKeyDown}
          className="nodrag text-center text-xs font-medium bg-background border border-border rounded px-1 py-0.5 outline-none focus:ring-1 focus:ring-ring mt-2"
          style={{ width: Math.max(80, dimensions.width + 20) }}
        />
      ) : (
        <span
          className="text-center text-xs font-medium text-muted-foreground leading-tight truncate cursor-text hover:text-foreground mt-2"
          style={{ maxWidth: Math.max(120, dimensions.width + 40) }}
          title={`${data.label} (double-click to rename)`}
          onDoubleClick={handleLabelDoubleClick}
        >
          {data.label}
        </span>
      )}

    </div>
  );
}

export default memo(WorkflowNode);
