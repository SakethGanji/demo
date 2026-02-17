/**
 * DynamicNodeForm - Schema-driven form generation for node parameters
 *
 * This component renders form fields dynamically based on the node's
 * property schema from the API (INodeProperty[]).
 */

import { useState, useId, useMemo, useCallback, useEffect, useRef, memo } from 'react';
import { Eye, EyeOff, Plus, Trash2, ChevronDown, ChevronUp, Info } from 'lucide-react';
import ExpressionEditor from './ExpressionEditor';
import CodeEditor from '@/shared/components/ui/code-editor';
import { WorkflowSelectorField } from './WorkflowSelectorField';
import { FilePathField } from './FilePathField';

// --- Inlined from formValidation.ts ---
interface FieldError {
  message: string;
}

function validateField(
  value: unknown,
  options: { required?: boolean; type?: string; min?: number; max?: number }
): FieldError | null {
  if (options.required) {
    if (value === undefined || value === null || value === '') {
      return { message: 'This field is required' };
    }
  }
  if (options.type === 'number' && value !== undefined && value !== null && value !== '') {
    const num = typeof value === 'number' ? value : parseFloat(String(value));
    if (isNaN(num)) return { message: 'Must be a valid number' };
    if (options.min !== undefined && num < options.min) return { message: `Minimum value is ${options.min}` };
    if (options.max !== undefined && num > options.max) return { message: `Maximum value is ${options.max}` };
  }
  return null;
}

// --- Inlined from useDebouncedCallback.ts ---
function useDebouncedCallback<T extends (...args: unknown[]) => void>(
  callback: T,
  delay: number
): T {
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  return useCallback(
    (...args: unknown[]) => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => callbackRef.current(...args), delay);
    },
    [delay]
  ) as T;
}

// Type definitions matching the API schema
// These are compatible with INodeProperty from workflow-engine
interface NodePropertyOption {
  name: string;
  value: string | number;
  description?: string;
}

interface NodePropertyTypeOptions {
  password?: boolean;
  rows?: number;
  language?: string;
  minValue?: number;
  maxValue?: number;
  step?: number;
  multipleValues?: boolean;
}

interface DisplayOptions {
  show?: Record<string, string[]>;
  hide?: Record<string, string[]>;
}

// Type for property field types
type NodePropertyType = 'string' | 'number' | 'boolean' | 'options' | 'json' | 'collection' | 'workflowSelector' | 'filePath';

interface NodeProperty {
  displayName: string;
  name: string;
  type: NodePropertyType;
  default?: unknown;
  required?: boolean;
  placeholder?: string;
  description?: string;
  options?: NodePropertyOption[];
  typeOptions?: NodePropertyTypeOptions;
  properties?: NodeProperty[];
  displayOptions?: DisplayOptions;
}

// Output schema types for expression autocomplete
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

// Export for consumers
export type { NodeProperty, OutputSchema };

// Shared small components
function FieldDescription({ text }: { text?: string }) {
  if (!text) return null;
  return (
    <div className="mt-1.5 flex items-start gap-1.5">
      <Info size={12} className="mt-0.5 flex-shrink-0 text-muted-foreground" />
      <p className="text-xs text-muted-foreground leading-relaxed">{text}</p>
    </div>
  );
}

function FieldErrorMessage({ error }: { error?: FieldError | null }) {
  if (!error) return null;
  return (
    <p className="mt-1 text-xs text-[var(--destructive)]">{error.message}</p>
  );
}

interface DynamicNodeFormProps {
  properties: NodeProperty[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  /** All parameter values - used for displayOptions evaluation */
  allValues?: Record<string, unknown>;
  /** Output schema from upstream node for expression autocomplete */
  upstreamSchema?: OutputSchema;
  /** Sample data from upstream node execution for preview */
  sampleData?: Record<string, unknown>[];
  /** All node execution data keyed by node name - for resolving $node["NodeName"].json expressions */
  allNodeData?: Record<string, Record<string, unknown>[]>;
}

export default memo(function DynamicNodeForm({
  properties,
  values,
  onChange,
  allValues,
  upstreamSchema,
  sampleData,
  allNodeData,
}: DynamicNodeFormProps) {
  const [fieldErrors, setFieldErrors] = useState<Record<string, FieldError | null>>({});
  const [touchedFields, setTouchedFields] = useState<Set<string>>(new Set());
  // Ref for touchedFields — keeps handleFieldChange stable across touches
  const touchedFieldsRef = useRef(touchedFields);
  touchedFieldsRef.current = touchedFields;

  // Debounced onChange for text-input types
  const debouncedOnChange = useDebouncedCallback(onChange, 300);

  // Determine which onChange to use based on field type
  const getOnChange = useCallback((property: NodeProperty) => {
    const immediateTypes: NodePropertyType[] = ['boolean', 'options', 'workflowSelector', 'filePath'];
    if (immediateTypes.includes(property.type)) {
      return onChange;
    }
    return debouncedOnChange;
  }, [onChange, debouncedOnChange]);

  const handleValidate = useCallback((name: string, property: NodeProperty, value: unknown) => {
    const error = validateField(value, {
      required: property.required,
      type: property.type,
      min: property.typeOptions?.minValue,
      max: property.typeOptions?.maxValue,
    });
    setFieldErrors((prev) => ({ ...prev, [name]: error }));
    return error;
  }, []);

  const handleBlur = useCallback((name: string, property: NodeProperty, value: unknown) => {
    setTouchedFields((prev) => new Set(prev).add(name));
    handleValidate(name, property, value);
  }, [handleValidate]);

  // Use ref for touchedFields to keep this callback stable
  const handleFieldChange = useCallback((property: NodeProperty, value: unknown) => {
    const fieldOnChange = getOnChange(property);
    fieldOnChange(property.name, value);
    // Re-validate if already touched (use ref to avoid dep on touchedFields state)
    if (touchedFieldsRef.current.has(property.name)) {
      handleValidate(property.name, property, value);
    }
  }, [getOnChange, handleValidate]);

  // Filter properties based on displayOptions
  const visibleProperties = properties.filter((prop) =>
    shouldShowProperty(prop, allValues || values)
  );

  return (
    <div className="space-y-3">
      {visibleProperties.map((property, index) => (
        <div key={property.name}>
          {/* Field grouping divider after 6th field when form is long */}
          {visibleProperties.length > 8 && index === 6 && (
            <div className="border-t border-border/50 my-1" />
          )}
          <PropertyField
            property={property}
            value={values[property.name]}
            onChange={(value) => handleFieldChange(property, value)}
            onBlur={() => handleBlur(property.name, property, values[property.name])}
            error={touchedFields.has(property.name) ? fieldErrors[property.name] : null}
            allValues={allValues || values}
            upstreamSchema={upstreamSchema}
            sampleData={sampleData}
            allNodeData={allNodeData}
          />
        </div>
      ))}
    </div>
  );
});

/**
 * Check if a property should be shown based on displayOptions
 */
function shouldShowProperty(
  property: NodeProperty,
  values: Record<string, unknown>
): boolean {
  const { displayOptions } = property;
  if (!displayOptions) return true;

  // Check 'show' conditions - property shown if ANY condition matches
  if (displayOptions.show) {
    for (const [field, allowedValues] of Object.entries(displayOptions.show)) {
      const currentValue = values[field];
      if (Array.isArray(allowedValues)) {
        if (!allowedValues.some(v => String(v) === String(currentValue))) {
          return false;
        }
      }
    }
  }

  // Check 'hide' conditions - property hidden if ANY condition matches
  if (displayOptions.hide) {
    for (const [field, hiddenValues] of Object.entries(displayOptions.hide)) {
      const currentValue = values[field];
      if (Array.isArray(hiddenValues)) {
        if (hiddenValues.some(v => String(v) === String(currentValue))) {
          return false;
        }
      }
    }
  }

  return true;
}

/**
 * Render a single property field based on its type
 */
interface PropertyFieldProps {
  property: NodeProperty;
  value: unknown;
  onChange: (value: unknown) => void;
  onBlur?: () => void;
  error?: FieldError | null;
  allValues: Record<string, unknown>;
  upstreamSchema?: OutputSchema;
  sampleData?: Record<string, unknown>[];
  allNodeData?: Record<string, Record<string, unknown>[]>;
}

const PropertyField = memo(function PropertyField({ property, value, onChange, onBlur, error, allValues, upstreamSchema, sampleData, allNodeData }: PropertyFieldProps) {
  const { type } = property;

  switch (type) {
    case 'string':
      return property.typeOptions?.password ? (
        <PasswordField
          property={property}
          value={(value as string) || ''}
          onChange={onChange}
          onBlur={onBlur}
          error={error}
        />
      ) : property.typeOptions?.rows && property.typeOptions.rows > 1 ? (
        <div>
          <ExpressionEditor
            label={property.displayName}
            value={(value as string) || ''}
            onChange={(v) => onChange(v)}
            placeholder={property.placeholder}
            outputSchema={upstreamSchema}
            sampleData={sampleData}
            allNodeData={allNodeData}
            onBlur={onBlur}
          />
          <FieldDescription text={property.description} />
          <FieldErrorMessage error={error} />
        </div>
      ) : (
        <StringField
          property={property}
          value={(value as string) || ''}
          onChange={onChange}
          onBlur={onBlur}
          error={error}
          upstreamSchema={upstreamSchema}
          sampleData={sampleData}
          allNodeData={allNodeData}
        />
      );

    case 'number':
      return (
        <NumberField
          property={property}
          value={value as number}
          onChange={onChange}
          onBlur={onBlur}
          error={error}
        />
      );

    case 'boolean':
      return (
        <BooleanField
          property={property}
          value={(value as boolean) || false}
          onChange={onChange}
        />
      );

    case 'options':
      return (
        <OptionsField
          property={property}
          value={value as string}
          onChange={onChange}
          onBlur={onBlur}
          error={error}
        />
      );

    case 'json':
      return (
        <JsonField
          property={property}
          value={value}
          onChange={onChange}
          onBlur={onBlur}
          error={error}
        />
      );

    case 'collection': {
      // Normalise: backend may store collection as {values:[…]} or plain array
      let collectionValue: unknown[] = [];
      if (Array.isArray(value)) {
        collectionValue = value;
      } else if (value && typeof value === 'object' && !Array.isArray(value)) {
        const obj = value as Record<string, unknown>;
        const inner = obj.values ?? Object.values(obj).find(Array.isArray);
        if (Array.isArray(inner)) collectionValue = inner;
      }
      return (
        <CollectionField
          property={property}
          value={collectionValue}
          onChange={onChange}
          allValues={allValues}
        />
      );
    }

    case 'workflowSelector':
      return (
        <WorkflowSelectorField
          property={property}
          value={value as string}
          onChange={onChange}
        />
      );

    case 'filePath':
      return (
        <FilePathField
          property={property}
          value={(value as string) || ''}
          onChange={onChange}
        />
      );

    default:
      return (
        <div className="text-sm text-muted-foreground">
          Unsupported field type: {type}
        </div>
      );
  }
});

// ============================================
// Field Components
// ============================================

function StringField({
  property,
  value,
  onChange,
  onBlur,
  error,
  upstreamSchema,
  sampleData,
  allNodeData,
}: {
  property: NodeProperty;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  error?: FieldError | null;
  upstreamSchema?: OutputSchema;
  sampleData?: Record<string, unknown>[];
  allNodeData?: Record<string, Record<string, unknown>[]>;
}) {
  const fieldId = useId();

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-sm font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-1">*</span>}
      </label>
      <ExpressionEditor
        id={fieldId}
        value={value}
        onChange={onChange}
        placeholder={property.placeholder}
        outputSchema={upstreamSchema}
        sampleData={sampleData}
        allNodeData={allNodeData}
        onBlur={onBlur}
      />
      <FieldDescription text={property.description} />
      <FieldErrorMessage error={error} />
    </div>
  );
}

function PasswordField({
  property,
  value,
  onChange,
  onBlur,
  error,
}: {
  property: NodeProperty;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  error?: FieldError | null;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const fieldId = useId();

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-sm font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-1">*</span>}
      </label>
      <div className="relative">
        <input
          id={fieldId}
          type={showPassword ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          placeholder={property.placeholder}
          className={`w-full rounded-lg border bg-background px-3 py-2 pr-10 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring ${
            error ? 'border-[var(--destructive)]' : 'border-input'
          }`}
        />
        <button
          type="button"
          onClick={() => setShowPassword(!showPassword)}
          className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground"
        >
          {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
      <FieldDescription text={property.description} />
      <FieldErrorMessage error={error} />
    </div>
  );
}

function NumberField({
  property,
  value,
  onChange,
  onBlur,
  error,
}: {
  property: NodeProperty;
  value: number | undefined;
  onChange: (value: number) => void;
  onBlur?: () => void;
  error?: FieldError | null;
}) {
  const { typeOptions } = property;
  const min = typeOptions?.minValue;
  const max = typeOptions?.maxValue;
  const step = typeOptions?.step ?? 1;
  const fieldId = useId();

  // Local string state for better UX — user can type freely
  const [localValue, setLocalValue] = useState(
    value !== undefined && value !== null ? String(value) : ''
  );
  const prevValueRef = useRef(value);

  // Sync localValue when external value prop changes (e.g., from undo or reset)
  useEffect(() => {
    if (value !== prevValueRef.current) {
      prevValueRef.current = value;
      setLocalValue(value !== undefined && value !== null ? String(value) : '');
    }
  }, [value]);

  // Local error for immediate feedback
  const localError = useMemo((): FieldError | null => {
    if (localValue === '') return null;
    const parsed = parseFloat(localValue);
    if (isNaN(parsed)) return { message: 'Must be a valid number' };
    if (min !== undefined && parsed < min) return { message: `Minimum value is ${min}` };
    if (max !== undefined && parsed > max) return { message: `Maximum value is ${max}` };
    return null;
  }, [localValue, min, max]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setLocalValue(raw);
    const parsed = parseFloat(raw);
    if (!isNaN(parsed)) {
      onChange(parsed);
    }
  };

  const displayError = localError || error;

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-sm font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-1">*</span>}
      </label>
      <input
        id={fieldId}
        type="number"
        value={localValue}
        onChange={handleChange}
        onBlur={onBlur}
        min={min}
        max={max}
        step={step}
        className={`w-full rounded-lg border bg-background px-3 py-2 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring ${
          displayError ? 'border-[var(--destructive)]' : 'border-input'
        }`}
      />
      <FieldDescription text={property.description} />
      <FieldErrorMessage error={displayError} />
    </div>
  );
}

function BooleanField({
  property,
  value,
  onChange,
}: {
  property: NodeProperty;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  const fieldId = useId();

  return (
    <label htmlFor={fieldId} className="flex items-center gap-2 cursor-pointer">
      <input
        id={fieldId}
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-input text-primary focus:ring-ring"
      />
      <div>
        <span className="text-sm text-foreground">{property.displayName}</span>
        <FieldDescription text={property.description} />
      </div>
    </label>
  );
}

function OptionsField({
  property,
  value,
  onChange,
  onBlur,
  error,
}: {
  property: NodeProperty;
  value: string | number | undefined;
  onChange: (value: string | number) => void;
  onBlur?: () => void;
  error?: FieldError | null;
}) {
  const options = property.options || [];
  const fieldId = useId();

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-sm font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-1">*</span>}
      </label>
      <select
        id={fieldId}
        value={String(value ?? property.default ?? '')}
        onChange={(e) => {
          // Try to preserve the original type (number vs string)
          const selectedOption = options.find((o) => String(o.value) === e.target.value);
          onChange(selectedOption?.value ?? e.target.value);
        }}
        onBlur={onBlur}
        className={`w-full rounded-lg border bg-background px-3 py-2 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring ${
          error ? 'border-[var(--destructive)]' : 'border-input'
        }`}
      >
        {options.map((option) => (
          <option key={String(option.value)} value={String(option.value)}>
            {option.name}
          </option>
        ))}
      </select>
      <FieldDescription text={property.description} />
      <FieldErrorMessage error={error} />
    </div>
  );
}

function JsonField({
  property,
  value,
  onChange,
  onBlur,
  error,
}: {
  property: NodeProperty;
  value: unknown;
  onChange: (value: unknown) => void;
  onBlur?: () => void;
  error?: FieldError | null;
}) {
  const rows = property.typeOptions?.rows ?? 6;
  const language = property.typeOptions?.language ?? 'json';

  // Convert value to string for editing
  const stringValue =
    typeof value === 'string'
      ? value
      : value !== undefined
        ? JSON.stringify(value, null, 2)
        : (property.default as string) ?? '';

  const handleChange = (newValue: string) => {
    // For JavaScript code, keep as string
    if (language === 'javascript') {
      onChange(newValue);
      return;
    }

    // For JSON, try to parse
    try {
      const parsed = JSON.parse(newValue);
      onChange(parsed);
    } catch {
      // Keep as string if not valid JSON
      onChange(newValue);
    }
  };

  // Calculate min height based on rows
  const minHeight = `${Math.max(rows * 24, 100)}px`;
  const maxHeight = `${Math.max(rows * 24, 300)}px`;

  return (
    <div onBlur={onBlur}>
      <label className="mb-1 block text-sm font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-1">*</span>}
      </label>
      <CodeEditor
        value={stringValue}
        onChange={handleChange}
        language={language === 'javascript' ? 'javascript' : 'json'}
        placeholder={property.placeholder}
        minHeight={minHeight}
        maxHeight={maxHeight}
      />
      <FieldDescription text={property.description} />
      <FieldErrorMessage error={error} />
    </div>
  );
}

function CollectionField({
  property,
  value,
  onChange,
  allValues,
}: {
  property: NodeProperty;
  value: unknown[];
  onChange: (value: unknown[]) => void;
  allValues: Record<string, unknown>;
}) {
  const [expandedItems, setExpandedItems] = useState<Record<number, boolean>>({});
  const nestedProperties = property.properties || [];
  const isMultiple = property.typeOptions?.multipleValues !== false;
  const fieldId = useId();

  const addItem = () => {
    const defaultItem: Record<string, unknown> = {};
    nestedProperties.forEach((prop) => {
      if (prop.default !== undefined) {
        defaultItem[prop.name] = prop.default;
      }
    });
    onChange([...value, defaultItem]);
    setExpandedItems({ ...expandedItems, [value.length]: true });
  };

  const removeItem = (index: number) => {
    const newValue = [...value];
    newValue.splice(index, 1);
    onChange(newValue);
  };

  const updateItem = (index: number, key: string, itemValue: unknown) => {
    const newValue = [...value];
    newValue[index] = { ...(newValue[index] as Record<string, unknown>), [key]: itemValue };
    onChange(newValue);
  };

  const toggleItem = (index: number) => {
    setExpandedItems({ ...expandedItems, [index]: !expandedItems[index] });
  };

  const moveItem = (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= value.length) return;
    const newValue = [...value];
    [newValue[index], newValue[targetIndex]] = [newValue[targetIndex], newValue[index]];
    // Update expanded state to follow the moved item
    const newExpanded = { ...expandedItems };
    const wasExpanded = expandedItems[index];
    const targetWasExpanded = expandedItems[targetIndex];
    newExpanded[index] = targetWasExpanded ?? true;
    newExpanded[targetIndex] = wasExpanded ?? true;
    setExpandedItems(newExpanded);
    onChange(newValue);
  };

  // Get preview text for a collapsed item
  const getItemPreview = (item: unknown): string => {
    if (!item || typeof item !== 'object') return '';
    const itemObj = item as Record<string, unknown>;
    // Use the first nested property's value as preview
    if (nestedProperties.length > 0) {
      const firstVal = itemObj[nestedProperties[0].name];
      if (firstVal !== undefined && firstVal !== null && firstVal !== '') {
        const str = String(firstVal);
        return str.length > 40 ? str.slice(0, 40) + '...' : str;
      }
    }
    return '';
  };

  return (
    <div>
      <label htmlFor={fieldId} className="mb-2 block text-sm font-medium text-foreground">
        {property.displayName}
      </label>

      <div className="space-y-2">
        {value.map((item, index) => {
          const itemValues = item as Record<string, unknown>;
          const isExpanded = expandedItems[index] ?? true;
          const preview = !isExpanded ? getItemPreview(item) : '';

          return (
            <div
              key={index}
              className="rounded-lg border border-border bg-muted/50"
            >
              <div className="flex items-center justify-between px-3 py-2">
                <button
                  type="button"
                  onClick={() => toggleItem(index)}
                  className="flex items-center gap-2 text-sm font-medium text-foreground min-w-0"
                >
                  {isExpanded ? <ChevronUp size={16} className="flex-shrink-0" /> : <ChevronDown size={16} className="flex-shrink-0" />}
                  <span>Item {index + 1}</span>
                  {preview && (
                    <span className="text-xs text-muted-foreground truncate">
                      — {preview}
                    </span>
                  )}
                </button>
                <div className="flex items-center gap-0.5">
                  <button
                    type="button"
                    onClick={() => moveItem(index, 'up')}
                    disabled={index === 0}
                    className="p-1 text-muted-foreground hover:text-foreground rounded disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Move up"
                  >
                    <ChevronUp size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveItem(index, 'down')}
                    disabled={index === value.length - 1}
                    className="p-1 text-muted-foreground hover:text-foreground rounded disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Move down"
                  >
                    <ChevronDown size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => removeItem(index)}
                    className="p-1 text-muted-foreground hover:text-destructive rounded"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {isExpanded && (
                <div className="border-t border-border px-3 py-3 space-y-3">
                  {nestedProperties.map((nestedProp) => (
                    <PropertyField
                      key={nestedProp.name}
                      property={nestedProp}
                      value={itemValues[nestedProp.name]}
                      onChange={(val) => updateItem(index, nestedProp.name, val)}
                      allValues={allValues}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {isMultiple && (
        <button
          type="button"
          onClick={addItem}
          className="mt-2 flex items-center gap-1 text-sm text-primary hover:underline"
        >
          <Plus size={14} />
          Add {property.displayName}
        </button>
      )}

      <FieldDescription text={property.description} />
    </div>
  );
}
