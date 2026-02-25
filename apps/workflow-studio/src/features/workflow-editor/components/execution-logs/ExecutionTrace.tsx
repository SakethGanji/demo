/**
 * Execution Trace
 *
 * Minimal recursive tree renderer for agent execution traces.
 * Clean rows with indentation, show more/less, and bordered code blocks.
 */

import { useState, useCallback } from 'react';
import {
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
} from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { TraceNode } from '../../types/workflow';

interface ExecutionTraceProps {
  tree: TraceNode;
}

export default function ExecutionTrace({ tree }: ExecutionTraceProps) {
  return (
    <div className="text-[11px]">
      <TraceRow node={tree} depth={0} isRoot />
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${((ms % 60000) / 1000).toFixed(0)}s`;
}

function getChildren(node: TraceNode): TraceNode[] {
  if ('children' in node && Array.isArray((node as { children?: unknown }).children)) {
    return (node as { children: TraceNode[] }).children;
  }
  return [];
}

function defaultExpanded(node: TraceNode, isRoot: boolean): boolean {
  if (isRoot) return true;
  if (node.kind === 'agent') return true;
  if (node.kind === 'iteration') return true;
  return false;
}

function hasExpandableContent(node: TraceNode): boolean {
  return node.kind === 'thinking' || node.kind === 'plan' || node.kind === 'reflect'
    || node.kind === 'response' || node.kind === 'tool_call'
    || (node.kind === 'validation' && !!node.errors?.length);
}

// ── TraceRow ────────────────────────────────────────────────────────────

interface TraceRowProps {
  node: TraceNode;
  depth: number;
  isRoot?: boolean;
}

function TraceRow({ node, depth, isRoot = false }: TraceRowProps) {
  const [expanded, setExpanded] = useState(() => defaultExpanded(node, isRoot));
  const [showDetail, setShowDetail] = useState(false);

  const children = getChildren(node);
  const collapsible = children.length > 0;
  const expandable = hasExpandableContent(node);

  const toggle = useCallback(() => {
    if (collapsible) setExpanded((v) => !v);
    else if (expandable) setShowDetail((v) => !v);
  }, [collapsible, expandable]);

  // Root agent — skip its own row, render children directly
  if (isRoot && node.kind === 'agent') {
    return (
      <>
        {children.map((child, i) => (
          <TraceRow key={`${child.kind}-${i}`} node={child} depth={depth} />
        ))}
      </>
    );
  }

  const isIteration = node.kind === 'iteration';
  const label = getLabel(node);
  const rightInfo = getRightInfo(node);

  return (
    <div className={cn(isIteration && 'mt-1 first:mt-0')}>
      {/* Iteration separator line */}
      {isIteration && depth === 0 && (
        <div className="border-t border-border/50 mx-1 mb-0.5" />
      )}

      {/* Row */}
      <div
        onClick={toggle}
        className={cn(
          'flex items-start gap-1 py-[3px] px-1 rounded-sm',
          (collapsible || expandable) && 'cursor-pointer hover:bg-accent/40',
        )}
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
      >
        {/* Toggle icon */}
        <span className="w-3.5 h-[18px] flex items-center justify-center shrink-0">
          {collapsible ? (
            expanded
              ? <ChevronDown size={11} className="text-muted-foreground" />
              : <ChevronRight size={11} className="text-muted-foreground" />
          ) : expandable ? (
            <span className="text-[8px] text-muted-foreground/60">{showDetail ? '\u25BC' : '\u25B6'}</span>
          ) : (
            <span className="text-muted-foreground/25 text-[6px]">{'\u2022'}</span>
          )}
        </span>

        {/* Label */}
        <span className="flex-1 min-w-0 break-words leading-[18px]">
          {label}
        </span>

        {/* Right-side info */}
        {rightInfo && (
          <span className="text-[10px] text-muted-foreground/70 tabular-nums shrink-0 ml-2 leading-[18px]">
            {rightInfo}
          </span>
        )}
      </div>

      {/* Expandable detail */}
      {expandable && showDetail && !collapsible && (
        <div className="pb-1" style={{ paddingLeft: `${depth * 14 + 22}px`, paddingRight: '4px' }}>
          <DetailContent node={node} />
        </div>
      )}

      {/* Children */}
      {collapsible && expanded && (
        <div className={cn(isIteration && 'pb-0.5')}>
          {children.map((child, i) => (
            <TraceRow key={`${child.kind}-${i}`} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Label ───────────────────────────────────────────────────────────────

function getLabel(node: TraceNode): React.ReactNode {
  switch (node.kind) {
    case 'agent':
      return <span className="font-medium">{node.name}</span>;

    case 'iteration':
      return <span className="font-medium text-muted-foreground">Iteration {node.number}</span>;

    case 'thinking':
      return (
        <span>
          <span className="font-medium">Thinking</span>
          <span className="text-muted-foreground ml-1">{node.content}</span>
        </span>
      );

    case 'plan':
      return (
        <span>
          <span className="font-medium">Plan</span>
          <span className="text-muted-foreground ml-1">{node.content}</span>
        </span>
      );

    case 'reflect':
      return (
        <span>
          <span className="font-medium">Reflect</span>
          <span className="text-muted-foreground ml-1">{node.content}</span>
        </span>
      );

    case 'tool_call':
      return (
        <span>
          <span className="font-mono font-medium">{node.tool}</span>
          {node.input && (
            <span className="text-muted-foreground ml-1 font-mono text-[10px]">
              {typeof node.input === 'string' ? node.input : JSON.stringify(node.input)}
            </span>
          )}
        </span>
      );

    case 'spawn':
      return (
        <span>
          <span className="font-medium">{node.skill ? 'delegate_to_skill' : 'spawn_agent'}</span>
          <span className="text-muted-foreground ml-1">{node.skill || node.task}</span>
        </span>
      );

    case 'validation':
      return (
        <span>
          <span className="font-medium">Validation</span>
          <span className={cn('ml-1', node.status === 'success' ? 'text-[var(--success)]' : 'text-destructive')}>
            {node.status}
          </span>
        </span>
      );

    case 'response':
      return (
        <span>
          <span className="font-medium">Response</span>
          <span className="text-muted-foreground ml-1">{node.content}</span>
        </span>
      );

    default:
      return <span className="text-muted-foreground">{node.kind}</span>;
  }
}

// ── Right info ──────────────────────────────────────────────────────────

function getRightInfo(node: TraceNode): string | null {
  const parts: string[] = [];

  if ('duration' in node && (node as { duration?: number }).duration) {
    parts.push(formatMs((node as { duration: number }).duration));
  }
  if (node.kind === 'agent' && node.iterations) {
    parts.push(`${node.iterations} iter`);
  }
  if (node.kind === 'tool_call') {
    if (node.isError) parts.push('error');
    else if (node.result !== undefined) parts.push('ok');
  }

  return parts.length > 0 ? parts.join('  ') : null;
}

// ── Detail content ──────────────────────────────────────────────────────

function DetailContent({ node }: { node: TraceNode }) {
  switch (node.kind) {
    case 'thinking':
    case 'plan':
    case 'reflect':
    case 'response':
      return (
        <div className="mt-1 mb-1">
          <CodeBlock content={node.content} />
        </div>
      );

    case 'tool_call':
      return (
        <div className="mt-1 mb-1 space-y-1.5">
          <div>
            <span className="text-[10px] text-muted-foreground/70">Input</span>
            <CodeBlock content={typeof node.input === 'string' ? node.input : JSON.stringify(node.input, null, 2)} />
          </div>
          {node.result !== undefined && (
            <div>
              <span className={cn('text-[10px]', node.isError ? 'text-destructive/70' : 'text-muted-foreground/70')}>
                {node.isError ? 'Error' : 'Result'}
              </span>
              <CodeBlock content={typeof node.result === 'string' ? node.result : JSON.stringify(node.result, null, 2)} />
            </div>
          )}
        </div>
      );

    case 'validation':
      if (node.errors?.length) {
        return (
          <div className="mt-1 mb-1 p-2 rounded border border-destructive/20 bg-destructive/5 space-y-0.5">
            {node.errors.map((err, i) => (
              <p key={i} className="text-[10px] text-destructive">{err}</p>
            ))}
          </div>
        );
      }
      return null;

    default:
      return null;
  }
}

// ── Code block ──────────────────────────────────────────────────────────

function CodeBlock({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="relative group mt-0.5">
      <button
        onClick={(e) => { e.stopPropagation(); handleCopy(); }}
        className="absolute top-1.5 right-1.5 p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
      >
        {copied ? <Check size={9} /> : <Copy size={9} />}
      </button>
      <pre className="p-2 rounded border border-border/60 bg-muted/40 text-[10px] leading-[1.5] overflow-auto font-mono whitespace-pre-wrap break-words max-h-48 text-muted-foreground">
        {content || <span className="text-muted-foreground/30 italic">empty</span>}
      </pre>
    </div>
  );
}
