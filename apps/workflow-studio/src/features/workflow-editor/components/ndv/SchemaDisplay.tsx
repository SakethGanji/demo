/**
 * SchemaDisplay - Shows node output schema structure for drag-and-drop
 * Used when there's no execution data yet, so users can still see available fields.
 */

import { useState, useCallback, memo } from 'react';
import { GripVertical, ChevronRight, ChevronDown, Copy, Check } from 'lucide-react';

interface SchemaProperty {
  type: string;
  description?: string;
  properties?: Record<string, SchemaProperty>;
  items?: SchemaProperty;
}

interface OutputSchema {
  type: string;
  description?: string;
  properties?: Record<string, SchemaProperty>;
  items?: SchemaProperty;
}

interface SchemaDisplayProps {
  schema: OutputSchema;
  basePath: string;
}

// Type badge colors — using theme CSS variables for consistency
const typeBadgeColors: Record<string, string> = {
  string: 'bg-[var(--success)]/10 text-[var(--success)]',
  number: 'bg-primary/10 text-primary',
  boolean: 'bg-[var(--node-flow)]/10 text-[var(--node-flow)]',
  object: 'bg-[var(--warning)]/10 text-[var(--warning)]',
  array: 'bg-[var(--node-trigger)]/10 text-[var(--node-trigger)]',
  unknown: 'bg-muted text-muted-foreground',
};

export default function SchemaDisplay({ schema, basePath }: SchemaDisplayProps) {
  const handleDragStart = useCallback((e: React.DragEvent, fieldPath: string) => {
    e.dataTransfer.setData('text/plain', fieldPath);
    e.dataTransfer.setData('application/x-field-path', fieldPath);
    e.dataTransfer.effectAllowed = 'copy';
  }, []);

  if (!schema.properties || Object.keys(schema.properties).length === 0) {
    return (
      <div className="text-xs text-muted-foreground text-center py-4">
        No schema defined for this node
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
          Schema
        </span>
        <span className="text-xs text-muted-foreground">
          Output structure (drag to use)
        </span>
      </div>
      <div className="rounded-md border border-border bg-card">
        {Object.entries(schema.properties).map(([key, prop]) => (
          <SchemaFieldRow
            key={`${basePath}-${key}`}
            name={key}
            property={prop}
            path={`${basePath}.${key}`}
            depth={0}
            onDragStart={handleDragStart}
          />
        ))}
      </div>
    </div>
  );
}

interface SchemaFieldRowProps {
  name: string;
  property: SchemaProperty;
  path: string;
  depth: number;
  onDragStart: (e: React.DragEvent, path: string) => void;
}

const SchemaFieldRow = memo(function SchemaFieldRow({
  name,
  property,
  path,
  depth,
  onDragStart,
}: SchemaFieldRowProps) {
  const [isExpanded, setIsExpanded] = useState(depth < 2);
  const [copied, setCopied] = useState(false);

  const hasChildren = property.type === 'object' && property.properties && Object.keys(property.properties).length > 0;
  const paddingLeft = depth * 12 + 8;

  const handleCopyPath = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      const expression = `{{ ${path} }}`;
      navigator.clipboard.writeText(expression);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    },
    [path]
  );

  return (
    <>
      <div
        draggable
        onDragStart={(e) => onDragStart(e, path)}
        className="flex items-center justify-between border-b border-border last:border-b-0 hover:bg-primary/5 cursor-grab transition-colors group"
        style={{ paddingLeft }}
      >
        <div className="flex items-center gap-0.5 py-1.5 pr-1 flex-1 min-w-0">
          {/* Drag handle */}
          <GripVertical
            size={12}
            className="opacity-20 group-hover:opacity-60 flex-shrink-0 text-muted-foreground"
          />

          {/* Expand/collapse for nested */}
          {hasChildren ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setIsExpanded(!isExpanded);
              }}
              className="p-0.5 hover:bg-accent rounded flex-shrink-0"
            >
              {isExpanded ? (
                <ChevronDown size={12} className="text-muted-foreground" />
              ) : (
                <ChevronRight size={12} className="text-muted-foreground" />
              )}
            </button>
          ) : (
            <span className="w-4" />
          )}

          {/* Field name */}
          <span className="font-mono text-xs text-foreground truncate">{name}</span>

          {/* Description tooltip */}
          {property.description && (
            <span className="text-[10px] text-muted-foreground truncate ml-1">
              - {property.description}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 pr-2 py-1.5">
          {/* Copy button */}
          <button
            onClick={handleCopyPath}
            className="p-0.5 rounded hover:bg-accent opacity-0 group-hover:opacity-100 transition-opacity"
            title={`Copy {{ ${path} }}`}
          >
            {copied ? (
              <Check size={12} className="text-[var(--success)]" />
            ) : (
              <Copy size={12} className="text-muted-foreground" />
            )}
          </button>

          {/* Type badge */}
          <span
            className={`rounded px-1.5 py-0.5 font-mono text-[10px] flex-shrink-0 ${
              typeBadgeColors[property.type] || 'bg-muted text-muted-foreground'
            }`}
          >
            {property.type}
          </span>
        </div>
      </div>

      {/* Render children if expanded */}
      {hasChildren && isExpanded && property.properties && (
        <>
          {Object.entries(property.properties).map(([childKey, childProp]) => (
            <SchemaFieldRow
              key={`${path}-${childKey}`}
              name={childKey}
              property={childProp}
              path={`${path}.${childKey}`}
              depth={depth + 1}
              onDragStart={onDragStart}
            />
          ))}
        </>
      )}
    </>
  );
});
