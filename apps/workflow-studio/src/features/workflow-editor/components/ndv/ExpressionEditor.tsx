import { useState, useRef, useEffect, useMemo, memo, useCallback, createContext, useContext } from 'react';
import { X, ChevronDown, Eye, EyeOff, Maximize2 } from 'lucide-react';
import FullscreenExpressionEditor from './FullscreenExpressionEditor';
import { useActiveNodeName, useShapesByNode } from '../../hooks/useWorkflowSelectors';
import { contractSuggestions } from '../../lib/contractSuggestions';
import { detectTrigger, filterByToken, applyTrigger, type ExpressionTrigger } from '../../lib/expressionTrigger';

/** When true, editors start with the resolved-value preview shown (the glass node sheet turns it on). */
export const ExpressionPreviewContext = createContext(false);
/** Fallback sample data for editors nested where the form does not pass it down (e.g. inside a collection). */
export const ExpressionSampleContext = createContext<{ sampleData?: Record<string, unknown>[]; allNodeData?: Record<string, Record<string, unknown>[]> }>({});

// Type definitions for output schema (matching workflow-engine)
interface OutputSchemaProperty {
  type: 'string' | 'number' | 'boolean' | 'object' | 'array' | 'unknown';
  description?: string;
  properties?: Record<string, OutputSchemaProperty>;
  items?: OutputSchemaProperty;
}

interface OutputSchema {
  type: 'object' | 'array' | 'string' | 'number' | 'boolean' | 'unknown';
  properties?: Record<string, OutputSchemaProperty>;
  items?: OutputSchemaProperty;
  description?: string;
  passthrough?: boolean;
}

interface ExpressionEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  /** Optional id for label htmlFor association */
  id?: string;
  /** Blur callback for validation */
  onBlur?: () => void;
  /** Output schema from connected upstream node for autocomplete */
  outputSchema?: OutputSchema;
  /** Sample data from upstream node execution for preview */
  sampleData?: Record<string, unknown>[];
  /** All node execution data keyed by node name - for resolving $node["NodeName"].json expressions */
  allNodeData?: Record<string, Record<string, unknown>[]>;
  /** Minimum visible rows when expanded (default 2) */
  rows?: number;
  /** Maximum rows before scrolling (default 15) */
  maxRows?: number;
}

// Simple expression syntax highlighting
function highlightExpression(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  // Match {{ expression }} patterns
  const regex = /\{\{([^}]+)\}\}/g;
  let match;

  while ((match = regex.exec(text)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(
        <span key={`text-${lastIndex}`}>
          {text.slice(lastIndex, match.index)}
        </span>
      );
    }
    // Add the highlighted expression
    parts.push(
      <span
        key={`expr-${match.index}`}
        className="bg-primary/20 text-primary px-0.5 rounded font-mono"
      >
        {match[0]}
      </span>
    );
    lastIndex = regex.lastIndex;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(<span key={`text-${lastIndex}`}>{text.slice(lastIndex)}</span>);
  }

  return parts.length > 0 ? parts : [text];
}

// Resolve expression paths against sample data
function resolveExpression(
  text: string,
  sampleData: Record<string, unknown>[] | undefined,
  allNodeData?: Record<string, Record<string, unknown>[]>
): string {
  return text.replace(/\{\{\s*([^}]+)\s*\}\}/g, (match, expr) => {
    const trimmedExpr = expr.trim();

    // Handle $node["NodeName"].json.path expressions
    const nodeMatch = trimmedExpr.match(/^\$node\["([^"]+)"\]\.json(?:\.(.+))?$/);
    if (nodeMatch) {
      const nodeName = nodeMatch[1];
      const path = nodeMatch[2]; // may be undefined if just $node["Name"].json

      // Look up the node's data
      const nodeData = allNodeData?.[nodeName];
      if (nodeData && nodeData.length > 0) {
        const firstItem = nodeData[0];
        if (path) {
          const value = getNestedValue(firstItem, path);
          if (value !== undefined) {
            return typeof value === 'object' ? JSON.stringify(value) : String(value);
          }
        } else {
          // Just $node["Name"].json - return the whole object
          return JSON.stringify(firstItem);
        }
      }
      return match; // Return original if can't resolve
    }

    // Handle $json.path expressions (immediate upstream node)
    if (trimmedExpr.startsWith('$json.') && sampleData && sampleData.length > 0) {
      const path = trimmedExpr.slice(6); // Remove '$json.'
      const value = getNestedValue(sampleData[0], path);
      if (value !== undefined) {
        return typeof value === 'object' ? JSON.stringify(value) : String(value);
      }
    }

    // Handle $json directly
    if (trimmedExpr === '$json' && sampleData && sampleData.length > 0) {
      return JSON.stringify(sampleData[0]);
    }

    return match; // Return original if can't resolve
  });
}

// Get nested value from object by dot path
function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.');
  let current: unknown = obj;

  for (const part of parts) {
    if (current === null || current === undefined) return undefined;
    if (typeof current !== 'object') return undefined;
    current = (current as Record<string, unknown>)[part];
  }

  return current;
}

// Base expression suggestions (always available)
const baseExpressionSuggestions = [
  { label: '$json', description: 'Current item JSON data', category: 'data' },
  { label: '$input.item', description: 'Current input item', category: 'data' },
  { label: '$input.all()', description: 'All input items', category: 'data' },
  { label: '$node["NodeName"].json', description: 'Data from specific node', category: 'data' },
  { label: '$now', description: 'Current timestamp', category: 'helper' },
  { label: '$today', description: "Today's date", category: 'helper' },
  { label: '$env.VARIABLE', description: 'Environment variable', category: 'helper' },
  { label: '$runIndex', description: 'Current run index', category: 'helper' },
  { label: '$itemIndex', description: 'Current item index', category: 'helper' },
];

/**
 * Generate field suggestions from an output schema
 */
function generateSchemaFields(
  schema: OutputSchema | OutputSchemaProperty | undefined,
  prefix: string = '$json'
): Array<{ label: string; description: string; category: string }> {
  if (!schema) return [];

  const suggestions: Array<{ label: string; description: string; category: string }> = [];

  if (schema.type === 'object' && schema.properties) {
    for (const [key, prop] of Object.entries(schema.properties)) {
      const fieldPath = `${prefix}.${key}`;
      const typedProp = prop as OutputSchemaProperty;
      const typeLabel = typedProp.type === 'unknown' ? 'any' : typedProp.type;

      suggestions.push({
        label: fieldPath,
        description: typedProp.description || `${typeLabel} field`,
        category: 'field',
      });

      // Recurse for nested objects (max 2 levels deep)
      if (typedProp.type === 'object' && typedProp.properties && prefix.split('.').length < 3) {
        suggestions.push(...generateSchemaFields(typedProp, fieldPath));
      }
    }
  }

  return suggestions;
}

export default memo(function ExpressionEditor({
  value,
  onChange,
  placeholder = 'Value or {{ expression }}...',
  label,
  id,
  onBlur,
  outputSchema,
  sampleData: sampleDataProp,
  allNodeData: allNodeDataProp,
  rows = 2,
  maxRows = 15,
}: ExpressionEditorProps) {
  const sampleCtx = useContext(ExpressionSampleContext);
  const sampleData = sampleDataProp ?? sampleCtx.sampleData;
  const allNodeData = allNodeDataProp ?? sampleCtx.allNodeData;
  // Local state for responsive typing — prevents parent re-renders from resetting input
  const [localValue, setLocalValue] = useState(value);
  const prevValueRef = useRef(value);

  // Sync from prop when it changes externally (e.g., after debounce round-trip)
  if (value !== prevValueRef.current) {
    prevValueRef.current = value;
    setLocalValue(value);
  }

  const handleChange = useCallback((newValue: string) => {
    setLocalValue(newValue);
    onChange(newValue);
  }, [onChange]);

  const [isExpanded, setIsExpanded] = useState(false);
  // Open suggestions: the typed token being completed, and whether the user opened it by hand (link)
  const [suggest, setSuggest] = useState<{ trigger: ExpressionTrigger; manual: boolean } | null>(null);
  const showSuggestions = suggest !== null;
  const setShowSuggestions = (open: boolean) => { if (!open) setSuggest(null); };
  const [cursorPosition, setCursorPosition] = useState(0);
  const [isDragOver, setIsDragOver] = useState(false);
  const [showPreview, setShowPreview] = useState(useContext(ExpressionPreviewContext));
  const [activeIndex, setActiveIndex] = useState(0);
  const [showFullscreen, setShowFullscreen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);

  // Line height in px for row calculations
  const lineHeight = 20;
  const minHeight = rows * lineHeight;
  const maxHeight = maxRows * lineHeight;

  // Auto-grow textarea to fit content
  const adjustTextareaHeight = useCallback(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = Math.max(minHeight, Math.min(textarea.scrollHeight, maxHeight)) + 'px';
  }, [minHeight, maxHeight]);

  // Re-adjust height when expanded state or value changes externally
  useEffect(() => {
    if (isExpanded) {
      // Small delay to allow DOM to settle after expand
      requestAnimationFrame(adjustTextareaHeight);
    }
  }, [isExpanded, localValue, adjustTextareaHeight]);

  const hasExpression = localValue.includes('{{');
  // Can preview if we have expressions AND either sample data or allNodeData
  const canPreview = hasExpression && ((sampleData && sampleData.length > 0) || (allNodeData && Object.keys(allNodeData).length > 0));

  // Resolved/preview value
  const previewValue = useMemo(() => {
    if (!showPreview || !canPreview) return null;
    return resolveExpression(localValue, sampleData, allNodeData);
  }, [showPreview, canPreview, localValue, sampleData, allNodeData]);

  // Handle drag events - use refs to avoid re-renders during drag
  const dragCountRef = useRef(0);

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current++;
    if (dragCountRef.current === 1) {
      if (e.dataTransfer.types.includes('application/x-field-path') || e.dataTransfer.types.includes('text/plain')) {
        setIsDragOver(true);
      }
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
    // Don't setState here - fires too frequently
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current--;
    if (dragCountRef.current === 0) {
      setIsDragOver(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current = 0;
    setIsDragOver(false);

    // Try to get the field path from custom type first, then fall back to text
    let fieldPath = e.dataTransfer.getData('application/x-field-path');
    if (!fieldPath) {
      fieldPath = e.dataTransfer.getData('text/plain');
    }

    // Accept paths starting with $json, $node, or other valid expression prefixes
    if (fieldPath && (fieldPath.startsWith('$json') || fieldPath.startsWith('$node') || fieldPath.startsWith('$'))) {
      // Insert the expression at the end of current value (or replace if empty)
      const expression = `{{ ${fieldPath} }}`;
      if (localValue.trim()) {
        handleChange(`${localValue} ${expression}`);
      } else {
        handleChange(expression);
      }

      // Auto-expand to show the inserted expression
      setIsExpanded(true);
    }
  };

  // Generate suggestions from output schema
  const activeNodeName = useActiveNodeName();
  const shapesByNode = useShapesByNode();
  const expressionSuggestions = useMemo(() => {
    const schemaFields: Array<{ label: string; description: string; category: string; source?: 'contract' }> =
      generateSchemaFields(outputSchema);
    // No run data for a node: fall back to the contract shapes from the live check.
    const seen = new Set(schemaFields.map((f) => f.label));
    for (const c of contractSuggestions({
      currentNode: activeNodeName ?? undefined,
      shapesByNode,
      sampleData,
      allNodeData,
    })) {
      if (!seen.has(c.label)) schemaFields.push(c);
    }

    // If we have schema fields, show them first, then base suggestions
    if (schemaFields.length > 0) {
      return [...schemaFields, ...baseExpressionSuggestions];
    }

    return baseExpressionSuggestions;
  }, [outputSchema, activeNodeName, shapesByNode, sampleData, allNodeData]);

  // Flatten suggestions into a single list with section headers for keyboard nav
  const flatSuggestions = useMemo(() => {
    const items: Array<{ label: string; description: string; category: string; source?: 'contract' }> = [];
    const categories = ['field', 'data', 'helper'] as const;
    for (const cat of categories) {
      const catItems = expressionSuggestions.filter((s) => s.category === cat);
      if (catItems.length > 0) {
        items.push(...catItems);
      }
    }
    return items;
  }, [expressionSuggestions]);

  const allFlat = flatSuggestions;
  const visibleSuggestions = useMemo(
    () => (suggest ? filterByToken(allFlat, suggest.trigger.token) : []),
    [allFlat, suggest],
  );

  const insertExpression = (expr: string) => {
    if (!suggest) return;
    const cursor = inputRef.current?.selectionStart ?? cursorPosition;
    const { value: newValue, cursor: newPos } = applyTrigger(localValue, cursor, suggest.trigger, expr);
    handleChange(newValue);
    setSuggest(null);

    // Focus and set cursor position after the inserted expression
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.setSelectionRange(newPos, newPos);
      }
    }, 0);
  };

  /** Re-evaluates the auto-open rules for the text and cursor. A hand-opened list stays until closed. */
  const syncTrigger = (text: string, cursor: number) => {
    setCursorPosition(cursor);
    const t = detectTrigger(text, cursor);
    setSuggest((prev) => {
      if (t) return { trigger: t, manual: false };
      return prev?.manual ? prev : null;
    });
  };

  const openByHand = () => {
    const cursor = inputRef.current?.selectionStart ?? localValue.length;
    const before = localValue.slice(0, cursor);
    const t = detectTrigger(localValue, cursor) ?? {
      start: cursor,
      token: '',
      insideBraces: before.lastIndexOf('{{') > before.lastIndexOf('}}'),
    };
    setSuggest({ trigger: t, manual: true });
  };

  // Reset activeIndex when suggestions open
  const suggestToken = suggest?.trigger.token;
  useEffect(() => {
    if (showSuggestions) {
      setActiveIndex(0);
    }
  }, [showSuggestions, suggestToken]);

  useEffect(() => {
    const handleClickOutside = () => {
      if (showSuggestions) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [showSuggestions]);

  // Scroll active suggestion into view
  useEffect(() => {
    if (showSuggestions && suggestionsRef.current) {
      const activeEl = suggestionsRef.current.querySelector('[data-active="true"]');
      if (activeEl) {
        activeEl.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [activeIndex, showSuggestions]);

  // Keyboard navigation for suggestions
  const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!showSuggestions) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.nativeEvent.stopImmediatePropagation();
      setSuggest(null);
      return;
    }
    if (visibleSuggestions.length === 0) return;

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActiveIndex((prev) => (prev + 1) % visibleSuggestions.length);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIndex((prev) => (prev - 1 + visibleSuggestions.length) % visibleSuggestions.length);
        break;
      case 'Enter':
      case 'Tab':
        e.preventDefault();
        insertExpression(visibleSuggestions[Math.min(activeIndex, visibleSuggestions.length - 1)].label);
        break;
    }
  };

  // Build category header positions for rendering
  const categoryHeaders = useMemo(() => {
    const headers: Map<number, string> = new Map();
    let currentCat = '';
    const categoryLabels: Record<string, string> = { field: 'Fields', data: 'Data', helper: 'Helpers' };
    visibleSuggestions.forEach((s, i) => {
      if (s.category !== currentCat) {
        currentCat = s.category;
        headers.set(i, categoryLabels[s.category] || s.category);
      }
    });
    return headers;
  }, [visibleSuggestions]);

  return (
    <div className="space-y-1">
      {label && (
        <label htmlFor={id} className="text-xs font-medium text-foreground/80">{label}</label>
      )}

      <div className="relative">
        {/* Main input with drop zone */}
        <div
          ref={containerRef}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onBlur={(e) => {
            // Collapse when focus leaves the entire container (not when clicking buttons inside it)
            if (!containerRef.current?.contains(e.relatedTarget as Node)) {
              setIsExpanded(false);
              setShowSuggestions(false);
              onBlur?.();
            }
          }}
          className={`
            field relative flex items-start transition-shadow
            ${isExpanded ? 'shadow-focus' : ''}
            ${isDragOver ? 'bg-brand-soft shadow-focus' : ''}
          `}
        >
          {/* Input area */}
          {isExpanded ? (
            <textarea
              ref={inputRef}
              id={id}
              value={localValue}
              onChange={(e) => {
                handleChange(e.target.value);
                syncTrigger(e.target.value, e.target.selectionStart);
              }}
              onInput={adjustTextareaHeight}
              onSelect={(e) => {
                const ta = e.target as HTMLTextAreaElement;
                if (suggest && !suggest.manual) syncTrigger(ta.value, ta.selectionStart);
                else setCursorPosition(ta.selectionStart);
              }}
              onKeyDown={handleTextareaKeyDown}
              placeholder={placeholder}
              style={{ minHeight: `${minHeight}px`, maxHeight: `${maxHeight}px` }}
              className="flex-1 bg-transparent px-2.5 py-1.5 pr-6 text-[13px] focus:outline-none resize-y font-mono"
            />
          ) : (
            <div
              className="flex-1 px-2.5 py-1.5 pr-6 text-[13px] cursor-text min-h-[28px] flex items-center"
              onClick={() => {
                setIsExpanded(true);
                // Focus textarea after expansion
                setTimeout(() => inputRef.current?.focus(), 0);
              }}
            >
              {localValue ? (
                <span className="line-clamp-2 text-xs">
                  {hasExpression ? highlightExpression(localValue) : localValue}
                </span>
              ) : (
                <span className="text-muted-foreground text-xs">{placeholder}</span>
              )}
            </div>
          )}

          {/* Preview / Clear / Expand buttons */}
          <div className="flex-shrink-0 flex items-center gap-0.5 p-0.5">
            {/* Preview toggle - only show if we have expressions and sample data */}
            {canPreview && (
              <button
                type="button"
                onClick={() => setShowPreview(!showPreview)}
                className={`p-0.5 rounded-chip transition-colors ${
                  showPreview
                    ? 'text-warning-text bg-warning/13'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                title={showPreview ? 'Show expression' : 'Preview resolved value'}
              >
                {showPreview ? <EyeOff size={12} /> : <Eye size={12} />}
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowFullscreen(true)}
              className="p-0.5 text-muted-foreground hover:text-foreground rounded-chip"
              title="Edit fullscreen"
            >
              <Maximize2 size={12} />
            </button>
            {localValue && (
              <button
                type="button"
                onClick={() => handleChange('')}
                className="p-0.5 text-muted-foreground hover:text-foreground rounded-chip"
                title="Clear"
              >
                <X size={12} />
              </button>
            )}
            <button
              type="button"
              onClick={() => setIsExpanded(!isExpanded)}
              className="p-0.5 text-muted-foreground hover:text-foreground rounded-chip"
              title={isExpanded ? 'Collapse' : 'Expand'}
            >
              <ChevronDown
                size={12}
                className={`transition-transform ${isExpanded ? 'rotate-180' : ''}`}
              />
            </button>
          </div>

          {/* Drop indicator overlay */}
          {isDragOver && (
            <div className="absolute inset-0 flex items-center justify-center rounded-ctl bg-brand-soft pointer-events-none">
              <span className="text-xs font-medium text-primary">Drop to insert</span>
            </div>
          )}
        </div>

        {/* Preview display */}
        {showPreview && previewValue && (
          <div className="mt-1 rounded-ctl bg-warning-soft px-2 py-1" data-testid="expr-preview">
            <div className="flex items-center gap-1 mb-0.5">
              <Eye size={10} className="text-warning-text" />
              <span className="text-[10.5px] font-medium text-warning-text">Preview</span>
            </div>
            <p className="text-xs font-mono text-foreground break-all">{previewValue}</p>
          </div>
        )}

        {/* Expression suggestions dropdown */}
        {isExpanded && (
          <div className="mt-1">
            <button
              type="button"
              // mousedown would blur the textarea and collapse the editor before the click lands
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                e.stopPropagation();
                openByHand();
              }}
              className="text-[10px] text-primary hover:underline"
            >
              Insert variable...
            </button>

            {showSuggestions && visibleSuggestions.length > 0 && (
              <div
                ref={suggestionsRef}
                data-testid="expression-suggestions"
                onMouseDown={(e) => e.preventDefault()}
                className="glass-overlay absolute z-50 mt-1 w-full max-h-48 overflow-auto p-1"
                onClick={(e) => e.stopPropagation()}
              >
                {visibleSuggestions.map((suggestion, idx) => (
                  <div key={suggestion.label}>
                    {/* Category header */}
                    {categoryHeaders.has(idx) && (
                      <div className="px-2 py-1 text-[11px] font-medium text-muted-foreground">
                        {categoryHeaders.get(idx)}
                      </div>
                    )}
                    <button
                      type="button"
                      data-active={idx === activeIndex}
                      data-source={suggestion.source}
                      onClick={() => insertExpression(suggestion.label)}
                      className={`w-full px-2 py-1.5 text-left flex items-center justify-between gap-1 ${
                        idx === activeIndex ? 'bg-raise shadow-raise-sm rounded-chip' : 'rounded-chip hover:bg-(--hover-fill)'
                      }`}
                    >
                      <code className="text-xs font-mono text-primary truncate">
                        {suggestion.label}
                      </code>
                      {suggestion.source === 'contract' && (
                        <span className="shrink-0 text-[10px] text-muted-foreground">from contract</span>
                      )}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Fullscreen editor overlay */}
      <FullscreenExpressionEditor
        open={showFullscreen}
        onOpenChange={setShowFullscreen}
        value={localValue}
        onChange={handleChange}
        label={label}
        placeholder={placeholder}
      />
    </div>
  );
});
