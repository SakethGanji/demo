/**
 * SchemaDisplay - Shows node output schema structure for drag-and-drop
 * Used when there's no execution data yet, so users can still see available fields.
 */

import { fieldRow } from './fieldRow';
import { useState, useCallback, memo } from 'react';
import { GripVertical, ChevronRight, ChevronDown, Copy, Check } from 'lucide-react';

interface SchemaProperty {
  type: string;
  description?: string;
  properties?: Record<string, SchemaProperty>;
  items?: SchemaProperty;
}

export interface OutputSchema {
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
  string: 'bg-hue-data/10 text-hue-data',
  number: 'bg-hue-backend/10 text-hue-backend',
  boolean: 'bg-hue-flow/10 text-hue-flow',
  object: 'bg-hue-secrets/10 text-hue-secrets',
  array: 'bg-hue-runs/10 text-hue-runs',
  unknown: 'bg-raise text-muted-foreground',
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
        <span className="inline-flex items-center rounded-chip bg-brand-soft px-1.5 py-0.5 text-[10.5px] font-medium text-primary">
          Schema
        </span>
        <span className="text-xs text-muted-foreground">
          Output structure (drag to use)
        </span>
      </div>
      <div>
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
        className={fieldRow}
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
              className="p-0.5 hover:bg-(--hover-fill) rounded-chip flex-shrink-0"
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
            className="p-0.5 rounded-chip hover:bg-(--hover-fill) opacity-0 group-hover:opacity-100 transition-opacity"
            title={`Copy {{ ${path} }}`}
          >
            {copied ? (
              <Check size={12} className="text-success" />
            ) : (
              <Copy size={12} className="text-muted-foreground" />
            )}
          </button>

          {/* Type badge */}
          <span
            className={`rounded-chip px-1.5 py-0.5 font-mono text-[10.5px] flex-shrink-0 ${
              typeBadgeColors[property.type] || 'bg-raise text-muted-foreground'
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
