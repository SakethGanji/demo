import { useMemo, useState, memo, useCallback } from 'react';
import { GripVertical, ChevronRight, ChevronDown, Copy, Check } from 'lucide-react';
import JsonViewer from '@/shared/components/ui/json-viewer';

interface RunDataDisplayProps {
  data: Record<string, unknown>[];
  mode: 'json' | 'schema';
  /** Base path for expression generation. Default: '$json' */
  basePath?: string;
}

// Type for nested schema structure
interface SchemaNode {
  type: string;
  children?: Record<string, SchemaNode>;
  path: string;
}

export default function RunDataDisplay({ data, mode, basePath = '$json' }: RunDataDisplayProps) {
  // Generate nested schema from data for tree view
  const schema = useMemo(() => {
    const buildSchema = (
      obj: Record<string, unknown>,
      currentPath: string = basePath
    ): Record<string, SchemaNode> => {
      const result: Record<string, SchemaNode> = {};

      Object.entries(obj).forEach(([key, value]) => {
        const path = `${currentPath}.${key}`;
        const type = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;

        if (type === 'object' && value !== null) {
          result[key] = {
            type,
            path,
            children: buildSchema(value as Record<string, unknown>, path),
          };
        } else if (type === 'array' && Array.isArray(value) && value.length > 0) {
          const firstItem = value[0];
          if (firstItem && typeof firstItem === 'object' && !Array.isArray(firstItem)) {
            result[key] = {
              type,
              path,
              children: buildSchema(firstItem as Record<string, unknown>, `${path}[0]`),
            };
          } else {
            result[key] = { type, path };
          }
        } else {
          result[key] = { type, path };
        }
      });

      return result;
    };

    // Merge schema from all items
    const merged: Record<string, unknown> = {};
    data.forEach((item) => {
      Object.entries(item).forEach(([key, value]) => {
        if (!(key in merged)) {
          merged[key] = value;
        }
      });
    });

    return buildSchema(merged);
  }, [data, basePath]);

  // Handle drag start for field - use native drag image for better performance
  const handleDragStart = useCallback((e: React.DragEvent, fieldPath: string) => {
    e.dataTransfer.setData('text/plain', fieldPath);
    e.dataTransfer.setData('application/x-field-path', fieldPath);
    e.dataTransfer.effectAllowed = 'copy';
    // Use native drag ghost (the element itself) - much smoother than custom drag image
  }, []);

  if (mode === 'json') {
    return (
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center rounded bg-[var(--success)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--success)]">
            JSON
          </span>
          <span className="text-xs text-muted-foreground">
            {data.length} item{data.length !== 1 ? 's' : ''}
          </span>
        </div>
        <JsonViewer value={data} maxHeight="calc(100vh - 300px)" />
      </div>
    );
  }

  // Schema mode (default)
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-muted-foreground">
        {data.length} item{data.length !== 1 ? 's' : ''} · Drag to map
      </p>
      <div className="rounded-md border border-border bg-card">
        {Object.entries(schema).map(([key, node]) => (
          <SchemaFieldRow
            key={`${basePath}-${key}`}
            name={key}
            node={node}
            depth={0}
            onDragStart={handleDragStart}
          />
        ))}
      </div>
    </div>
  );
}

// Type badge colors — using theme CSS variables for consistency
const typeBadgeColors: Record<string, string> = {
  string: 'bg-[var(--success)]/10 text-[var(--success)]',
  number: 'bg-primary/10 text-primary',
  boolean: 'bg-[var(--node-flow)]/10 text-[var(--node-flow)]',
  object: 'bg-[var(--warning)]/10 text-[var(--warning)]',
  array: 'bg-[var(--node-trigger)]/10 text-[var(--node-trigger)]',
  null: 'bg-muted text-muted-foreground',
};

// Schema field row component with expand/collapse for nested fields
interface SchemaFieldRowProps {
  name: string;
  node: SchemaNode;
  depth: number;
  onDragStart: (e: React.DragEvent, path: string) => void;
}

const SchemaFieldRow = memo(function SchemaFieldRow({ name, node, depth, onDragStart }: SchemaFieldRowProps) {
  const [isExpanded, setIsExpanded] = useState(depth < 2); // Auto-expand first 2 levels
  const [copied, setCopied] = useState(false);
  const hasChildren = node.children && Object.keys(node.children).length > 0;
  const paddingLeft = depth * 12 + 8;

  const handleCopyPath = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    const expression = `{{ ${node.path} }}`;
    navigator.clipboard.writeText(expression);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [node.path]);

  return (
    <>
      <div
        draggable
        onDragStart={(e) => onDragStart(e, node.path)}
        className="flex items-center justify-between border-b border-border last:border-b-0 hover:bg-primary/5 cursor-grab transition-colors group"
        style={{ paddingLeft }}
      >
        <div className="flex items-center gap-0.5 py-1.5 pr-1 flex-1 min-w-0">
          {/* Drag handle */}
          <GripVertical size={12} className="opacity-20 group-hover:opacity-60 flex-shrink-0 text-muted-foreground" />

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
            <span className="w-4" /> // Smaller spacer
          )}

          {/* Field name */}
          <span className="font-mono text-xs text-foreground truncate">{name}</span>
        </div>

        <div className="flex items-center gap-1 pr-2 py-1.5">
          {/* Copy button */}
          <button
            onClick={handleCopyPath}
            className="p-0.5 rounded hover:bg-accent opacity-0 group-hover:opacity-100 transition-opacity"
            title={`Copy {{ ${node.path} }}`}
          >
            {copied ? (
              <Check size={12} className="text-[var(--success)]" />
            ) : (
              <Copy size={12} className="text-muted-foreground" />
            )}
          </button>

          {/* Type badge - compact */}
          <span
            className={`rounded px-1.5 py-0.5 font-mono text-[10px] flex-shrink-0 ${typeBadgeColors[node.type] || 'bg-muted text-muted-foreground'}`}
          >
            {node.type}
          </span>
        </div>
      </div>

      {/* Render children if expanded */}
      {hasChildren && isExpanded && (
        <>
          {Object.entries(node.children!).map(([childKey, childNode]) => (
            <SchemaFieldRow
              key={childKey}
              name={childKey}
              node={childNode}
              depth={depth + 1}
              onDragStart={onDragStart}
            />
          ))}
        </>
      )}
    </>
  );
});
