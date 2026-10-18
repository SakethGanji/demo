import { memo, useCallback } from 'react';
import { GripVertical } from 'lucide-react';
import { shapeFields, type Shape } from '../../lib/shapes';

interface ContractFieldListProps {
  shape: Shape;
  /** When set, rows are draggable and show the expression path (`$json`, `$node["X"].json`). */
  basePath?: string;
}

/** Flat field list for a shape (path + type). Rows carry `data-source="contract"`. */
const ContractFieldList = memo(function ContractFieldList({ shape, basePath }: ContractFieldListProps) {
  const fields = shapeFields(shape);
  const onDragStart = useCallback((e: React.DragEvent, path: string) => {
    e.dataTransfer.setData('text/plain', path);
    e.dataTransfer.setData('application/x-field-path', path);
    e.dataTransfer.effectAllowed = 'copy';
  }, []);

  if (fields.length === 0) {
    return <p className="py-2 text-center text-[12px] text-muted-foreground">No known fields</p>;
  }
  return (
    <div data-source="contract" data-testid="contract-fields">
      {fields.map((f) => {
        const full = basePath ? `${basePath}.${f.path}` : f.path;
        return (
          <div
            key={f.path}
            data-source="contract"
            data-field={f.path}
            draggable={!!basePath}
            onDragStart={basePath ? (e) => onDragStart(e, full) : undefined}
            className={`group flex items-center justify-between gap-2 border-b border-hairline px-2 py-1.5 last:border-b-0 ${basePath ? 'row-hover cursor-grab' : ''}`}
          >
            <span className="flex min-w-0 items-center gap-0.5">
              {basePath && <GripVertical size={12} className="shrink-0 text-muted-foreground opacity-20 group-hover:opacity-60" />}
              <span className="truncate font-mono text-xs text-foreground">{f.path}</span>
            </span>
            <span className="shrink-0 rounded-chip bg-raise px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground">{f.type}</span>
          </div>
        );
      })}
    </div>
  );
});

export default ContractFieldList;
