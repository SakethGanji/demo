import { memo, useState, useMemo, useRef, useEffect } from 'react';
import { Handle, Position, useUpdateNodeInternals, type NodeProps, type Node } from '@xyflow/react';
import { Plus, Check, X, AlertTriangle, FileCheck2, FlaskConical, Clock } from 'lucide-react';
import { Tip } from '@/shared/components/ui/tooltip';
import { useStageStore, type StageNodeData } from '@/features/ai-builder/stage/stageStore';
import { round as roundTokens } from '@/shared/motion/tokens';
import { motionOff } from '@/shared/motion/testFlag';
import { NodeAssembly, AssemblyIcon, AssemblyLabel, SettleFlash, Brackets, PopBadge } from './NodeAssembly';
import { useStageActive } from '../useStageActive';
import { settleDelay } from '../stageGeometry';
import { useCheckStore } from '../../../stores/checkStore';
import { useTestStore } from '../../../stores/testStore';
import { useNodeAiStore } from '@/features/ai-builder/nodeAiStore';
import { nodeTestBadge } from '../../../lib/tests';
import { useEditorLayoutStore } from '../../../stores/editorLayoutStore';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { useHistoryStore } from '../../../stores/historyStore';
import { useConvertSubworkflowStore } from '../../../stores/convertSubworkflowStore';
import type { WorkflowNodeData } from '../../../types/workflow';
import {
  getNodeStyles,
  getNodeShapeConfig,
  calculateHandlePositions,
  calculateNodeDimensions,
} from '../../../lib/nodeStyles';
import { normalizeNodeGroup } from '../../../lib/nodeConfig';
import { NodeChips } from '../../interface/NodeChips';
import { SubworkflowBadge } from './SubworkflowBadge';
import { getIconForNode } from '../../../lib/nodeIcons';
import { isTriggerType } from '../../../lib/nodeConfig';
import {
  useNodeExecution,
  useHasInputConnection,
  useConnectedOutputHandles,
} from '../../../hooks/useWorkflowSelectors';


// Status badge component for success/error states
const StatusBadge = ({ status }: { status: 'success' | 'error' | 'waiting' }) => {
  const isSuccess = status === 'success';
  const isWaiting = status === 'waiting';
  return (
    <div
      className={`
        absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full
        flex items-center justify-center text-primary-foreground
        shadow-raise-sm animate-badge-pop z-10
        ${isWaiting ? 'bg-warning' : isSuccess ? 'bg-success' : 'bg-destructive'}
      `}
    >
      {isWaiting ? <Clock size={10} strokeWidth={3} /> : isSuccess ? <Check size={10} strokeWidth={3} /> : <X size={10} strokeWidth={3} />}
    </div>
  );
};


function WorkflowNode({ id, data, selected, positionAbsoluteX }: NodeProps<Node<WorkflowNodeData>>) {
  const [isHovered, setIsHovered] = useState(false);
  const [isEditingLabel, setIsEditingLabel] = useState(false);
  const labelInputRef = useRef<HTMLInputElement>(null);

  const openForConnection = useEditorLayoutStore((s) => s.openForConnection);
  // While an AI stage is active the canvas shows the proposal, so run, check and test state of the draft reads as idle.
  const stageActive = useStageActive();
  const liveExecutionData = useNodeExecution(id);
  const executionData = stageActive ? null : liveExecutionData;
  const isDragging = useWorkflowStore((s) => s.draggedNodeType !== null);
  const liveExecActive = useWorkflowStore((s) => s.isAnyNodeRunning);
  const isExecActive = stageActive ? false : liveExecActive;
  const anim = ((data as StageNodeData).anim ?? 'idle') as NonNullable<StageNodeData['anim']>;
  const animFields = (data as StageNodeData).animFields;
  const animScale = (data as StageNodeData).animScale ?? 1;
  const animCompact = !!(data as StageNodeData).animCompact;
  const animTone = (data as StageNodeData).animTone ?? 'primary';
  const reducedAnim = useStageStore((s) => s.active && s.nodes.length > roundTokens.reducedAbove);
  // While the AI builds this node the real glass, blur and shadow wait: the scan layer prints the body instead (plan §3.1)
  const building = stageActive && (anim === 'enter' || anim === 'exit') && !reducedAnim && !motionOff();
  const stageHover = useStageStore((s) => s.active && s.hoverNode === data.name);
  const settling = useStageStore((s) => s.mode === 'settling');
  const reviewing = useStageStore((s) => s.mode === 'review');
  const sweep = useStageStore((s) => s.sweep);
  const stageMinX = useStageStore((s) => (s.mode === 'settling' || s.mode === 'review' ? Math.min(...s.nodes.map((n) => n.position.x)) : 0));
  // Review sweep: plays once per bump (a played build reaching review), then is gated off so a remount never replays it
  const [sweepOn, setSweepOn] = useState(false);
  const [seenSweep, setSeenSweep] = useState(0);
  if (sweep !== seenSweep) {
    setSeenSweep(sweep);
    setSweepOn(stageActive && sweep > 0);
  }
  useEffect(() => {
    if (!sweepOn) return;
    const t = setTimeout(() => setSweepOn(false), 1500);
    return () => clearTimeout(t);
  }, [sweepOn]);
  const stageBadge = useStageStore((s) => (s.active ? s.badges[data.name] : undefined));
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);
  // Version-history diff marker: added (green), changed (amber), removed (faded ghost)
  const historyMark = useHistoryStore((s) => s.marks[id]);
  const stageMark = useStageStore((s) => (s.active ? s.marks[id] : undefined));
  const diffMark = stageMark ?? historyMark;
  // Convert-to-sub-workflow dialog: the nodes a refusal names
  const refusedByConvert = useConvertSubworkflowStore((s) => s.highlight.has(data.name));

  // Live-check findings for this node (reported by node name). Info findings draw no mark.
  const liveCheckFindings = useCheckStore((s) => s.findingsByNodeMap[data.name]);
  const checkFindings = stageActive ? undefined : liveCheckFindings;
  const checkMarks = useMemo(() => (checkFindings ?? []).filter((f) => f.severity !== 'info'), [checkFindings]);
  const checkSeverity = checkMarks.some((f) => f.severity === 'error') ? 'error' : checkMarks.length ? 'warning' : null;

  // Test badge: green when the tests that cover this node pass, red when any failure points at it
  const reviewReport = useNodeAiStore((s) => (s.phase === 'review' ? s.proposal?.report?.tests : null));
  const liveTestBadge = useTestStore((s) => nodeTestBadge(data.name, s.report, reviewReport));
  const testBadge = stageActive ? (stageBadge ? (stageBadge === 'passed' ? 'pass' : 'fail') : undefined) : liveTestBadge;

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
  const mainOutputCount = Math.max(1, data.outputCount ?? data.outputs?.length ?? 1);
  // `error_output` adds one more source handle (id "error") below the regular outputs
  const hasErrorOutput = data.onError === 'error_output';
  const outputCount = mainOutputCount + (hasErrorOutput ? 1 : 0);
  const isDisabled = !!data.disabled;
  const updateNodeInternals = useUpdateNodeInternals();
  // The handle set changed: React Flow must re-measure so edges attach to the new handle
  useEffect(() => {
    updateNodeInternals(id);
  }, [id, outputCount, updateNodeInternals]);

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
  const isWaiting = executionData?.status === 'waiting';
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
      const isErrorHandle = hasErrorOutput && index === mainOutputCount;
      const outputDef = isErrorHandle ? undefined : data.outputs?.[index];
      const handleId = isErrorHandle ? 'error' : outputDef?.name || `output-${index}`;
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
              data-testid={isErrorHandle ? 'node-error-handle' : undefined}
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
              data-port-dot
              className={`
                pointer-events-none flex items-center justify-center
                rounded-full transition-all duration-200 ease-out
                ${canExpand
                  ? 'h-5 w-5 canvas-chip'
                  : 'h-1.5 w-1.5 border-2'
                }
              `}
              style={{
                backgroundColor: canExpand ? undefined : isErrorHandle ? 'var(--destructive)' : styles.handleColor,
                borderColor: canExpand ? undefined : isErrorHandle ? 'var(--destructive)' : styles.handleColor,
              }}
            >
              {canExpand && (
                <Plus size={12} strokeWidth={2.5} className={isErrorHandle ? 'text-destructive' : undefined} />
              )}
            </div>
            {isErrorHandle && (
              <span className="pointer-events-none absolute left-full top-1/2 ml-1.5 -translate-y-1/2 text-[10px] font-medium leading-none text-destructive">
                error
              </span>
            )}
          </div>
        </div>
      );
    });
  };

  return (
    <div
      className={`relative flex flex-col items-center ${building ? 'node-constructing' : ''} ${isIdle ? 'node-exec-idle' : ''} ${diffMark === 'removed' ? 'node-ghost pointer-events-none select-none' : ''} ${isDisabled ? 'node-disabled' : ''}`}
      data-diff={diffMark}
      data-anim={stageActive && anim !== 'idle' ? anim : undefined}
      data-convert-refused={refusedByConvert || undefined}
      style={{
        transition: 'opacity 0.4s ease',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Drop zone indicator - shows on the left when node can accept a connection */}
      {canBeDropTarget && (
        <div
          className="absolute -left-8 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full border-2 border-dashed border-success bg-success/20 animate-pulse flex items-center justify-center"
          style={{ pointerEvents: 'none' }}
        >
          <Plus size={12} className="text-success" />
        </div>
      )}

      <div
        className={`relative cursor-grab transition-[box-shadow,transform] duration-(--d-base) ease-(--ease-out) flex items-center justify-center`}
        style={{
          height: dimensions.height,
          width: dimensions.width,
          backgroundColor: diffMark === 'removed' || building ? 'transparent' : styles.bgColor,
          outline: diffMark === 'removed' ? '2px dashed var(--muted-foreground)' : refusedByConvert ? '2px solid var(--destructive)' : undefined,
          outlineOffset: diffMark === 'removed' || refusedByConvert ? 2 : undefined,
          borderRadius: shapeConfig.borderRadius,
          // glass edge + elevation from the node tokens; selection / drop target add the ring on top
          boxShadow: building
            ? 'none'
            : stageHover
            ? '0 0 0 2px var(--primary), 0 0 0 5px color-mix(in srgb, var(--primary) 18%, transparent), var(--glow) 0 0 16px, var(--node-shadow)'
            : diffMark === 'added'
            ? '0 0 0 2px var(--success), var(--node-shadow)'
            : diffMark === 'changed'
              ? '0 0 0 2px var(--warning), var(--node-shadow)'
              : diffMark === 'removed'
                ? 'none'
                : canBeDropTarget
            ? '0 0 0 1px var(--success), 0 0 0 4px color-mix(in srgb, var(--success) 18%, transparent), 0 0 15px var(--success)'
            : selected
              ? `0 0 0 1px var(--ring), 0 0 0 4px color-mix(in srgb, var(--ring) 16%, transparent), var(--node-shadow)`
              : `var(--node-edge), var(--node-shadow)`,
          transform: isHovered && !selected ? 'translateY(-1px)' : undefined,
        }}
      >
        {/* AI build layers: ring, shockwave, changed-field chip. Null when idle; none of them wraps a Handle. */}
        {stageActive && (
          <NodeAssembly
            id={id}
            anim={anim}
            width={dimensions.width}
            height={dimensions.height}
            radius={parseFloat(shapeConfig.borderRadius) || 12}
            reduced={reducedAnim}
            fields={animFields}
            fill={styles.bgColor}
            ports={{ inputs: isTrigger ? [] : inputPositions, outputs: outputPositions }}
            scale={animScale}
            compact={animCompact}
            tone={animTone}
          />
        )}
        {stageActive && reviewing && sweepOn && diffMark !== 'removed' && (
          <SettleFlash key={sweep} tone="primary" radius={parseFloat(shapeConfig.borderRadius) || 12} delay={0.3 + settleDelay(positionAbsoluteX, stageMinX)} />
        )}
        {stageActive && settling && diffMark !== 'removed' && (
          <>
            <SettleFlash radius={parseFloat(shapeConfig.borderRadius) || 12} delay={settleDelay(positionAbsoluteX, stageMinX)} />
            <Brackets width={dimensions.width} height={dimensions.height} mode="close" delay={settleDelay(positionAbsoluteX, stageMinX)} />
          </>
        )}

        {/* Execution ring overlays — clean box-shadow animations, no blur */}
        {isRunning && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              borderRadius: shapeConfig.borderRadius,
              // @ts-expect-error CSS custom property
              '--ring-color': 'var(--ring)',
              animation: 'node-running-ring 2s ease-in-out infinite',
            }}
          />
        )}
        {isSuccess && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              borderRadius: shapeConfig.borderRadius,
              animation: 'node-success-ring 0.6s ease-out',
            }}
          />
        )}
        {isError && (
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              borderRadius: shapeConfig.borderRadius,
              animation: 'node-error-ring 0.5s ease-out',
            }}
          />
        )}

        {/* Input Handles */}
        {renderInputHandles()}

        {/* Node Content - Icon only, centered */}
        <AssemblyIcon id={id} anim={stageActive ? anim : 'idle'} reduced={reducedAnim} scale={animScale} compact={animCompact}>
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full shadow-raise-sm transition-colors duration-(--d-slow) ${nodeGroup === 'ai' ? 'brand-fill' : ''}`}
            style={nodeGroup === 'ai' ? undefined : {
              backgroundColor: styles.iconBgColor,
              color: styles.iconFgColor,
            }}
          >
            <IconComponent size={20} />
          </div>
        </AssemblyIcon>

        {/* Status badges for success/error */}
        {isSuccess && <StatusBadge status="success" />}
        {isError && <StatusBadge status="error" />}
        {isWaiting && <StatusBadge status="waiting" />}

        {/* Live-check mark: red = error, amber = warning (top-left; the run status badge is top-right) */}
        {checkSeverity && (
          <Tip
            label={
              <span className="flex flex-col gap-0.5">
                {checkMarks.map((f, i) => (
                  <span key={i}>{f.message}</span>
                ))}
              </span>
            }
          >
            <div
              data-testid="node-check-mark"
              data-check={checkSeverity}
              className={`absolute -top-1.5 -left-1.5 z-10 flex h-4 w-4 items-center justify-center rounded-full text-primary-foreground shadow-raise-sm ${checkSeverity === 'error' ? 'bg-destructive' : 'bg-warning'}`}
            >
              <AlertTriangle size={9} strokeWidth={3} />
            </div>
          </Tip>
        )}

        {/* Approved learned contract */}
        {data.contract && (
          <Tip label={`Contract approved by ${data.contract.approved_by} on ${new Date(data.contract.approved_at).toLocaleDateString()}`}>
            <div
              data-testid="node-contract-badge"
              className="absolute -bottom-1.5 -left-1.5 z-10 flex h-4 w-4 items-center justify-center rounded-full bg-brand-soft text-primary shadow-raise-sm"
            >
              <FileCheck2 size={9} strokeWidth={2.5} />
            </div>
          </Tip>
        )}

        {/* Tests covering this node */}
        {testBadge && (
          <Tip label={testBadge === 'fail' ? 'A test fails at this node' : 'Tests covering this node pass'}>
            {stageActive ? (
              <PopBadge
                status={testBadge}
                className={`absolute -bottom-1.5 -right-1.5 z-10 flex h-4 w-4 items-center justify-center rounded-full text-primary-foreground shadow-raise-sm ${testBadge === 'fail' ? 'bg-destructive' : 'bg-success'}`}
              >
                <FlaskConical size={9} strokeWidth={2.5} />
              </PopBadge>
            ) : (
              <div
                data-testid="node-test-badge"
                data-status={testBadge}
                className={`absolute -bottom-1.5 -right-1.5 z-10 flex h-4 w-4 items-center justify-center rounded-full text-primary-foreground shadow-raise-sm ${testBadge === 'fail' ? 'bg-destructive' : 'bg-success'}`}
              >
                <FlaskConical size={9} strokeWidth={2.5} />
              </div>
            )}
          </Tip>
        )}

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
          className="nodrag text-center text-xs font-medium field rounded-chip px-1 py-0.5 mt-2"
          style={{ width: Math.max(80, dimensions.width + 20) }}
        />
      ) : (
        <AssemblyLabel
          anim={stageActive ? anim : 'idle'}
          reduced={reducedAnim}
          scale={animScale}
          compact={animCompact}
          text={data.label}
          className={`text-center text-xs font-medium text-muted-foreground leading-tight truncate cursor-text hover:text-foreground mt-2 ${isDisabled ? 'line-through' : ''} ${diffMark === 'removed' ? 'italic' : ''}`}
          style={{ maxWidth: Math.max(120, dimensions.width + 40) }}
          title={`${data.label} (double-click to rename)`}
          onDoubleClick={handleLabelDoubleClick}
        />
      )}

      {/* Stage 5 chips: Approval "N waiting" / "Custom UI", FormTrigger "Form · N fields" */}
      {(data.type === 'Approval' || data.type === 'FormTrigger') && (
        <NodeChips type={data.type} name={data.name} parameters={data.parameters} />
      )}

      {/* Stage 8: which sub-workflow and version an ExecuteWorkflow node calls */}
      {data.type === 'ExecuteWorkflow' && <SubworkflowBadge parameters={data.parameters as Record<string, unknown> | undefined} />}

    </div>
  );
}

export default memo(WorkflowNode);
