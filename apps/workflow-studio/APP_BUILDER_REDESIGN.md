# App Builder — Full Redesign Spec

## What This Is

A drag-and-drop internal tool builder (think Retool/Tooljet) embedded inside a workflow automation platform (n8n clone). Users compose UIs from components (tables, buttons, inputs, etc.), wire them to data queries (REST APIs or workflow executions), and bind data between components using `{{ expression }}` templates.

The goal is to build something architecturally capable of reaching Retool/Appsmith parity over time — including multi-page apps with navigation, sidebars, modals, and full layout control.

---

## Current Architecture

### Stack
- React + TypeScript, Zustand for state, Tailwind CSS, shadcn/ui
- `react-grid-layout` for drag/resize grid
- `react-resizable-panels` for editor layout (left panel, canvas, property panel, query editor)

### File Structure
```
src/features/app-builder/
├── components/
│   ├── AppBuilder.tsx        # Main layout shell (3-panel resizable)
│   ├── AppNavbar.tsx         # Top bar (name, save, mode toggle)
│   ├── GridCanvas.tsx        # The drag/drop grid canvas (react-grid-layout, 12 cols, 60px rows)
│   ├── LeftPanel.tsx         # Component list + query list (tabbed: UI/Queries)
│   ├── PropertyPanel.tsx     # Right panel — switch-case per component type
│   └── QueryEditor.tsx       # Bottom panel — REST/Workflow query config
├── renderers/
│   ├── index.ts              # Registry map: type -> renderer FC
│   ├── TableRenderer.tsx     # Table with search, row selection, auto-columns
│   ├── ButtonRenderer.tsx    # Button with onClick event execution
│   ├── TextRenderer.tsx      # Text/label with {{ }} template evaluation
│   ├── TextInputRenderer.tsx # Text input (basic)
│   └── SelectRenderer.tsx    # Dropdown select
├── stores/
│   └── appStore.ts           # Zustand store (components, queries, runtimeState, selections, mode)
├── lib/
│   ├── componentRegistry.ts  # Metadata: { type, displayName, icon, defaultProps, defaultSize }
│   └── evaluator.ts          # Client-side {{ }} dot-path resolver
└── types.ts                  # Re-exports from backendTypes.ts
```

### How It Works Today

**Component Registry** (`componentRegistry.ts`) — flat metadata array:
```ts
export const COMPONENT_REGISTRY: ComponentMeta[] = [
  {
    type: 'table',
    displayName: 'Table',
    icon: 'Table',
    defaultProps: { data: '', columns: 'auto', showSearch: true },
    defaultSize: { w: 8, h: 6 },
  },
  // ... button, textInput, text, select
];
```

**Renderer Lookup** (`renderers/index.ts`) — separate map from registry:
```ts
const RENDERERS: Record<string, FC<RendererProps>> = {
  table: TableRenderer,
  button: ButtonRenderer,
  // ...
};
```

**Property Panel** (`PropertyPanel.tsx`) — giant switch-case per component type, each with bespoke 50-80 line property forms hardcoded inline. Separate sub-components: TableProperties, ButtonProperties, TextInputProperties, TextProperties, SelectProperties.

**Renderers** — each is standalone, no shared contract. They each independently hook into Zustand for runtime state, event handling, and expression resolution.

**Type System** (`backendTypes.ts`):
```ts
interface AppComponent {
  id: string;
  type: 'table' | 'button' | 'textInput' | 'text' | 'select';  // hardcoded union
  props: Record<string, unknown>;   // untyped bag
  position: { x: number; y: number; w: number; h: number };
  events: Record<string, AppEvent[]>;
}

interface AppEvent {
  type: 'runQuery' | 'setValue' | 'navigate';  // only runQuery fully implemented
  queryId?: string;
  componentId?: string;
  value?: string;
}

interface AppQuery {
  id: string;
  name: string;
  type: 'rest' | 'workflow';
  config: Record<string, unknown>;
  runOnLoad: boolean;
}
```

**Runtime State** — flat key-value store in Zustand:
```ts
runtimeState: Record<string, Record<string, unknown>>
// { table1: { selectedRow: {...} }, textInput1: { value: "hello" }, query1: { data: [...], loading: false, error: null } }
```

**Expression System** — client-side dot-path evaluator for UI bindings:
```ts
// {{ query1.data }} -> looks up runtimeState.query1.data
evaluate(template: string, state: Record<string, unknown>): unknown
// Supports single expressions (returns typed value) and string interpolation
```
Server-side: converts `{{ x }}` to `{{ $json.x }}` for the n8n expression engine (used for query URL/body resolution via `/apps/run-query` endpoint).

**Query Execution Flow**:
1. Frontend calls `runQuery(queryId)` -> sets loading state
2. POST `/apps/run-query` with query config + `app_state` (runtimeState)
3. Backend resolves `{{ }}` templates via expression engine
4. REST: httpx executes HTTP call. Workflow: WorkflowRunner executes and returns last node output.
5. Results stored in `runtimeState[queryId] = { data, loading, error }`

**Save/Load**:
- Manual save only (no auto-save)
- App loaded via `/app-builder?id=app_123` route
- `loadApp()` populates Zustand, then `runOnLoadQueries()` fires

### Current Components

| Component | Props | Exposed State | Events |
|-----------|-------|---------------|--------|
| table | data, columns, showSearch | selectedRow | onRowSelect |
| button | label, variant | (none) | onClick |
| textInput | label, placeholder, defaultValue | value | (none) |
| text | content (supports `{{ }}`) | (none) | (none) |
| select | label, options, defaultValue | selectedValue | (none) |

### Backend

**Model** (`AppModel`): `id`, `name`, `description`, `definition` (JSON blob), `created_at`, `updated_at`

**Routes** (`/apps`): Full CRUD + `POST /apps/run-query` for query execution with expression resolution.

**Repository**: Simple async CRUD with timestamp-based IDs, ordered by `updated_at DESC`.

---

## Problems

1. **Adding a component requires 5+ files**: registry, renderer, renderers/index.ts, PropertyPanel switch-case, type union, icon map
2. **Property panel is a monolith**: switch-case with bespoke forms per component
3. **No shared base behavior**: each renderer duplicates Zustand integration, event handling, expression resolution
4. **Events not discoverable**: implicitly defined inside renderers, no central declaration. Only `runQuery` fully implemented (setValue, navigate are stubs)
5. **Exposed state invisible**: what each component writes to `runtimeState` is implicit
6. **Props untyped**: `Record<string, unknown>` with `as string` casts everywhere
7. **No style system**: "coming soon" placeholder in PropertyPanel
8. **Flat layout only**: no nesting, no containers, no tabs/modals — can't build real apps
9. **Expression engine is dot-path only**: can't do filters, ternaries, or function calls
10. **No page-level layout**: no navbar, sidebar, multi-page routing — can't build full apps like Retool
11. **No undo/redo**: single mistake requires manual reversal
12. **No auto-save**: manual save only, easy to lose work
13. **No deploy concept**: deploy button is a stub

---

## Phased Roadmap

### Phase 1: Component Definition System (BUILD NOW)
Single-file component definitions, ComponentWrapper, schema-driven PropertyPanel, declarative events with `$event` payloads, component methods, undo/redo.

### Phase 2: Tree Layout
Evolve from flat 2D grid to nested component tree. Containers, tabs, modals, forms — each with their own grid. `parentId` on components, drag-into-container support.

### Phase 3: App Frame, Pages & Navigation
Page-level layout primitives: app frame templates (sidebar nav, top nav, blank), multi-page support with routing, shared navigation config. This is what makes "build any website" possible.

### Phase 4: JS Expression Engine
Replace dot-path evaluator with sandboxed JS evaluation. Support `{{ query1.data.filter(r => r.active) }}`, ternaries, string methods.

### Phase 5: Reactive Dependency Graph
Build a DAG tracking which expressions depend on which state. When `query1.data` changes, only re-evaluate components that reference it — not all 50.

### Phase 6: Advanced Components & Polish
Form, Modal, Drawer, Tabs, Repeater/List, Chart, Image, DatePicker, etc. Auto-save, deploy/publish, theming. These become easy once the foundation is solid.

---

## Phase 1 Spec: Component Definition System

### Architecture Overview

```
ComponentDefinition (one file per component)
  ├── meta: { displayName, icon, category, defaultSize }
  ├── propSchema: PropField[]           -> drives PropertyPanel automatically
  ├── eventSchema: EventField[]         -> drives event wiring UI
  ├── exposedState: StateField[]        -> drives {{ }} autocomplete
  ├── methods: MethodField[]            -> callable from actions
  └── Component: FC<RendererProps<T>>   -> the actual renderer (dumb, no Zustand)

ComponentWrapper (one shared component)
  ├── evaluates all {{ }} props against runtimeState
  ├── handles hidden/disabled/tooltip universally
  ├── provides setState, fireEvent, registerMethods to renderer
  ├── memoizes all callbacks to prevent identity churn
  └── renderer never touches Zustand directly

PropertyPanel (generic, schema-driven)
  ├── reads propSchema -> renders controls grouped by section
  ├── reads eventSchema -> renders event wiring UI
  ├── reads exposedState -> shows copyable state references
  └── no switch-case, no per-component code

Registry
  └── Map<string, ComponentDefinition> — register once, lookup everywhere

ActionRegistry
  └── Map<string, ActionDefinition> — same pattern as components, but for event actions
  └── runQuery, setValue, navigate, callMethod, showModal, hideModal, showNotification, etc.

UndoManager
  └── snapshot components/queries before each mutation, push to history stack
```

### Type Definitions

```ts
// --- Property Panel Controls ---

type ControlType =
  | 'text'           // plain text input
  | 'expression'     // {{ }} input with live preview + autocomplete
  | 'switch'         // boolean toggle
  | 'select'         // dropdown
  | 'json'           // JSON editor (for options arrays, headers, etc.)
  | 'querySelector'  // dropdown of available queries, writes {{ queryId.data }}
  | 'color'          // color picker (Phase 6)
  | 'columnMap';     // column builder for tables (Phase 6)

interface PropField<TProps = Record<string, unknown>> {
  name: Extract<keyof TProps | keyof UniversalProps, string>;
  label: string;
  section: string;                    // free-form: 'Data', 'Content', 'Interaction', 'Layout', 'Style'
  control: ControlType;
  defaultValue: unknown;
  options?: { label: string; value: string }[];  // for 'select' control
  tooltip?: string;
  expectedType?: 'string' | 'number' | 'boolean' | 'array' | 'object';  // for expression validation
  hidden?: (props: Record<string, unknown>) => boolean;  // conditional visibility
}

// --- Events ---

interface EventField {
  name: string;       // 'onChange', 'onRowSelect', 'onClick'
  label: string;      // 'Row Select', 'Click'
  payloadSchema?: Record<string, 'string' | 'number' | 'boolean' | 'object'>;
  // e.g. { value: 'string' } for onChange, { row: 'object', index: 'number' } for onRowSelect
}

// --- Exposed State ---

interface StateField {
  name: string;       // 'selectedRow', 'value'
  type: 'string' | 'number' | 'boolean' | 'array' | 'object';
  description: string;
}

// --- Methods ---

interface MethodField {
  name: string;       // 'clear', 'focus', 'selectPage'
  label: string;
  description: string;
  args?: { name: string; type: string; description: string }[];
}

// --- Universal Props (every component gets these via ComponentWrapper) ---

interface UniversalProps {
  hidden?: boolean;    // hides in preview, reduced opacity in edit
  disabled?: boolean;  // reduced opacity + pointer-events: none
  tooltip?: string;    // native title tooltip
}

// --- What renderers receive ---

interface RendererProps<TProps> {
  id: string;
  props: TProps & UniversalProps;               // already evaluated, typed
  setState: (updates: Record<string, unknown>) => void;
  fireEvent: (name: string, payload?: Record<string, unknown>) => void;
  registerMethods: (methods: Record<string, (...args: any[]) => void>) => void;
}

// --- The Master Definition ---

interface ComponentDefinition<TProps extends Record<string, any> = Record<string, any>> {
  type: string;
  meta: {
    displayName: string;
    icon: string;
    category?: 'input' | 'display' | 'layout' | 'action';
    defaultSize: { w: number; h: number };
    isContainer?: boolean;   // true for Container, Tabs, Modal, Form (Phase 2)
  };
  propSchema: PropField<TProps>[];
  eventSchema: EventField[];
  exposedState: StateField[];
  methods: MethodField[];
  Component: FC<RendererProps<TProps>>;
}

// --- Action Definitions (registry pattern for event actions) ---

interface ActionParamField {
  name: string;
  label: string;
  control: 'text' | 'expression' | 'select' | 'querySelector' | 'componentSelector' | 'methodSelector';
  options?: { label: string; value: string }[];  // for 'select'
  dependsOn?: string;                            // e.g., methodSelector depends on componentSelector
}

interface ActionDefinition {
  type: string;                  // 'runQuery', 'navigate', 'callMethod', 'showNotification', etc.
  label: string;                 // 'Run Query', 'Navigate to Page', etc.
  icon: string;                  // lucide icon name
  paramsSchema: ActionParamField[];
  execute: (params: Record<string, unknown>, context: ActionContext) => void | Promise<void>;
}

interface ActionContext {
  $event?: Record<string, unknown>;   // payload from triggering event
  getState: () => AppStoreState;      // access to store
  evaluate: (expr: string) => unknown; // evaluate expressions in current scope
}

// Example action definitions:
//
// { type: 'runQuery',          label: 'Run Query',          paramsSchema: [{ name: 'queryId', control: 'querySelector' }] }
// { type: 'setValue',          label: 'Set Value',          paramsSchema: [{ name: 'componentId', control: 'componentSelector' }, { name: 'value', control: 'expression' }] }
// { type: 'navigate',         label: 'Navigate',           paramsSchema: [{ name: 'pageId', control: 'select' }] }
// { type: 'callMethod',       label: 'Call Method',        paramsSchema: [{ name: 'componentId', control: 'componentSelector' }, { name: 'method', control: 'methodSelector', dependsOn: 'componentId' }] }
// { type: 'showNotification', label: 'Show Notification',  paramsSchema: [{ name: 'message', control: 'expression' }, { name: 'type', control: 'select', options: [...] }] }
// { type: 'showModal',        label: 'Show Modal',         paramsSchema: [{ name: 'componentId', control: 'componentSelector' }] }
// { type: 'hideModal',        label: 'Hide Modal',         paramsSchema: [{ name: 'componentId', control: 'componentSelector' }] }
// { type: 'copyToClipboard',  label: 'Copy to Clipboard',  paramsSchema: [{ name: 'value', control: 'expression' }] }
// { type: 'openUrl',          label: 'Open URL',           paramsSchema: [{ name: 'url', control: 'expression' }, { name: 'newTab', control: 'switch' }] }
// { type: 'setGlobalVariable',label: 'Set Variable',       paramsSchema: [{ name: 'name', control: 'text' }, { name: 'value', control: 'expression' }] }

// --- Global Variables ---

interface GlobalVariable {
  id: string;
  name: string;           // accessible as {{ globals.name }}
  defaultValue: unknown;
  description?: string;
}
```

### ComponentWrapper

The single most critical piece. Individual renderers become "dumb" — they receive evaluated props and stable callbacks, never touch Zustand.

**Key design decisions:**
- All callbacks (`setState`, `fireEvent`, `registerMethods`) are **stable references** using `useCallback` + `getState()` to avoid stale closures and identity churn
- Renderers are **controlled** — they do NOT own local state for values that exist in runtimeState. The wrapper reads runtimeState, passes it as props. Renderers call `setState()` to update, which flows back through runtimeState on next render.
- Method cleanup on unmount is handled automatically

```tsx
// src/features/app-builder/runtime/ComponentWrapper.tsx

export function ComponentWrapper({ componentId }: { componentId: string }) {
  const componentConfig = useAppStore(s => s.components.find(c => c.id === componentId));
  const runtimeState = useAppStore(s => s.runtimeState);
  const mode = useAppStore(s => s.mode);

  const definition = getComponentDefinition(componentConfig.type);

  // 1. Evaluate all {{ }} props against runtime state
  const evaluatedProps = useMemo(() => {
    const resolved: Record<string, any> = {};
    for (const [key, rawValue] of Object.entries(componentConfig.props)) {
      if (typeof rawValue === 'string' && rawValue.includes('{{')) {
        resolved[key] = evaluate(rawValue, runtimeState);
      } else {
        resolved[key] = rawValue;
      }
    }
    return resolved;
  }, [componentConfig.props, runtimeState]);

  // 2. Merge runtime state values into evaluated props (for controlled renderers)
  //    e.g., textInput1's "value" from runtimeState overrides the static defaultValue
  const componentState = runtimeState[componentId] || {};
  const mergedProps = useMemo(() => {
    const merged = { ...evaluatedProps };
    // Exposed state fields that should flow back as props
    for (const field of definition.exposedState) {
      if (field.name in componentState) {
        merged[field.name] = componentState[field.name];
      }
    }
    return merged;
  }, [evaluatedProps, componentState, definition.exposedState]);

  // 3. Universal: hidden
  if (mergedProps.hidden === true && mode === 'preview') {
    return null;
  }

  // 4. Stable callbacks — use getState() to avoid stale closures
  const handleSetState = useCallback((updates: Record<string, unknown>) => {
    const { setRuntimeValue } = useAppStore.getState();
    for (const [key, value] of Object.entries(updates)) {
      setRuntimeValue(componentId, key, value);
    }
  }, [componentId]);

  const handleFireEvent = useCallback((eventName: string, payload?: Record<string, unknown>) => {
    const { components, executeEventActions } = useAppStore.getState();
    const comp = components.find(c => c.id === componentId);
    const actions = comp?.events?.[eventName] || [];
    executeEventActions(actions, { $event: payload });
  }, [componentId]);

  const handleRegisterMethods = useCallback((methods: Record<string, (...args: any[]) => void>) => {
    useAppStore.getState().registerComponentMethods(componentId, methods);
  }, [componentId]);

  // 5. Cleanup methods on unmount
  useEffect(() => {
    return () => {
      useAppStore.getState().unregisterComponentMethods(componentId);
    };
  }, [componentId]);

  const Renderer = definition.Component;

  return (
    <div
      className="w-full h-full relative"
      title={mergedProps.tooltip}
      style={{
        opacity: mergedProps.hidden && mode === 'edit' ? 0.4 : mergedProps.disabled ? 0.6 : 1,
        pointerEvents: mergedProps.disabled ? 'none' : 'auto',
      }}
    >
      <Renderer
        id={componentId}
        props={mergedProps}
        setState={handleSetState}
        fireEvent={handleFireEvent}
        registerMethods={handleRegisterMethods}
      />
    </div>
  );
}
```

### Example: TextInput Component (Single File, Controlled)

Renderers are **controlled** — no local `useState` for values that exist in runtimeState. This prevents state drift between local state and Zustand.

```tsx
// src/features/app-builder/definitions/TextInput.tsx

interface TextInputProps {
  label: string;
  placeholder: string;
  defaultValue: string;
  value: string;        // injected from runtimeState by ComponentWrapper
}

const TextInputRenderer: FC<RendererProps<TextInputProps>> = ({
  id, props, setState, fireEvent, registerMethods
}) => {
  // Initialize runtime state on mount
  useEffect(() => {
    setState({ value: props.defaultValue || '' });
  }, []);

  // Register methods once (stable callbacks, no stale closure risk)
  useEffect(() => {
    registerMethods({
      clear: () => setState({ value: '' }),
      focus: () => document.getElementById(`input-${id}`)?.focus(),
    });
  }, [id, registerMethods, setState]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setState({ value: val });
    fireEvent('onChange', { value: val });
  };

  // props.value comes from runtimeState via ComponentWrapper (controlled)
  const value = props.value ?? props.defaultValue ?? '';

  return (
    <div className="flex flex-col gap-1 p-1.5 h-full justify-center">
      {props.label && (
        <label className="text-xs font-medium text-muted-foreground">{props.label}</label>
      )}
      <Input
        id={`input-${id}`}
        value={value}
        onChange={handleChange}
        placeholder={props.placeholder}
        onBlur={() => fireEvent('onBlur')}
        className="h-7 text-xs"
      />
    </div>
  );
};

export const TextInputDefinition: ComponentDefinition<TextInputProps> = {
  type: 'textInput',
  meta: {
    displayName: 'Text Input',
    icon: 'TextCursorInput',
    category: 'input',
    defaultSize: { w: 4, h: 1 },
  },

  propSchema: [
    { name: 'defaultValue', label: 'Default Value', section: 'Data', control: 'expression', defaultValue: '' },
    { name: 'label', label: 'Label', section: 'Content', control: 'text', defaultValue: 'Label' },
    { name: 'placeholder', label: 'Placeholder', section: 'Content', control: 'text', defaultValue: 'Enter value...' },
    { name: 'disabled', label: 'Disabled', section: 'Interaction', control: 'switch', defaultValue: false },
    { name: 'hidden', label: 'Hidden', section: 'Layout', control: 'switch', defaultValue: false },
    { name: 'tooltip', label: 'Tooltip', section: 'Layout', control: 'text', defaultValue: '' },
  ],

  eventSchema: [
    { name: 'onChange', label: 'On Change', payloadSchema: { value: 'string' } },
    { name: 'onBlur', label: 'On Blur' },
  ],

  exposedState: [
    { name: 'value', type: 'string', description: 'Current text value' },
  ],

  methods: [
    { name: 'clear', label: 'Clear', description: 'Clears the input value' },
    { name: 'focus', label: 'Focus', description: 'Focuses the input' },
  ],

  Component: TextInputRenderer,
};
```

### Generic Property Panel

No switch-case. Reads `propSchema` and renders controls grouped by section.

```tsx
// src/features/app-builder/inspector/PropertyPanel.tsx

export function PropertyPanel({ componentId }: { componentId: string }) {
  const component = useAppStore(s => s.components.find(c => c.id === componentId));
  const definition = getComponentDefinition(component.type);

  // Group props by section
  const sections = groupBy(definition.propSchema, p => p.section);

  return (
    <div className="h-full bg-card border-l flex flex-col overflow-auto">
      {/* Header */}
      <div className="px-3 py-2 border-b">
        <h3 className="text-xs font-semibold">{component.id}</h3>
        <p className="text-[10px] text-muted-foreground">{definition.meta.displayName}</p>
      </div>

      {/* Props by section — collapsible groups */}
      {Object.entries(sections).map(([section, fields]) => (
        <CollapsibleSection key={section} title={section}>
          {fields
            .filter(f => !f.hidden || !f.hidden(component.props))
            .map(field => (
              <PropControl key={field.name} field={field} component={component} />
            ))}
        </CollapsibleSection>
      ))}

      {/* Events — auto-generated from eventSchema */}
      {definition.eventSchema.length > 0 && (
        <CollapsibleSection title="Events">
          {definition.eventSchema.map(event => (
            <EventActionBuilder key={event.name} event={event} component={component} />
          ))}
        </CollapsibleSection>
      )}

      {/* Exposed state — read-only reference, click to copy */}
      {definition.exposedState.length > 0 && (
        <CollapsibleSection title="State Reference" defaultOpen={false}>
          {definition.exposedState.map(s => (
            <CopyableReference key={s.name} value={`{{ ${component.id}.${s.name} }}`}>
              <span className="text-xs font-mono text-muted-foreground">
                {`{{ ${component.id}.${s.name} }}`}
              </span>
              <span className="text-[10px] text-muted-foreground/60 ml-1">
                {s.type} — {s.description}
              </span>
            </CopyableReference>
          ))}
        </CollapsibleSection>
      )}

      {/* Methods — read-only reference */}
      {definition.methods.length > 0 && (
        <CollapsibleSection title="Methods" defaultOpen={false}>
          {definition.methods.map(m => (
            <div key={m.name} className="text-xs text-muted-foreground">
              <span className="font-mono">{component.id}.{m.name}()</span>
              <span className="text-[10px] ml-1">— {m.description}</span>
            </div>
          ))}
        </CollapsibleSection>
      )}
    </div>
  );
}
```

### PropControl — Renders the Right Input for Each Control Type

```tsx
// src/features/app-builder/inspector/PropControl.tsx

function PropControl({ field, component }: { field: PropField; component: AppComponent }) {
  const updateProps = useAppStore(s => s.updateComponentProps);
  const value = component.props[field.name] ?? field.defaultValue;

  switch (field.control) {
    case 'text':
      return (
        <PropField label={field.label} tooltip={field.tooltip}>
          <Input value={value as string} onChange={e => updateProps(component.id, { [field.name]: e.target.value })} />
        </PropField>
      );

    case 'expression':
      return (
        <PropField label={field.label} tooltip={field.tooltip}>
          <ExpressionInput
            value={value as string}
            onChange={v => updateProps(component.id, { [field.name]: v })}
            expectedType={field.expectedType}
          />
        </PropField>
      );

    case 'switch':
      return (
        <PropField label={field.label} tooltip={field.tooltip}>
          <Switch checked={!!value} onCheckedChange={v => updateProps(component.id, { [field.name]: v })} />
        </PropField>
      );

    case 'select':
      return (
        <PropField label={field.label} tooltip={field.tooltip}>
          <Select value={value as string} options={field.options}
            onChange={v => updateProps(component.id, { [field.name]: v })} />
        </PropField>
      );

    case 'querySelector':
      return (
        <PropField label={field.label} tooltip={field.tooltip}>
          <QuerySelector value={value as string} onChange={v => updateProps(component.id, { [field.name]: v })} />
        </PropField>
      );

    case 'json':
      return (
        <PropField label={field.label} tooltip={field.tooltip}>
          <CodeEditor value={value as string} onChange={v => updateProps(component.id, { [field.name]: v })} />
        </PropField>
      );

    default:
      return null;
  }
}
```

### Registration System

```ts
// src/features/app-builder/registry.ts

import { TextInputDefinition } from './definitions/TextInput';
import { TableDefinition } from './definitions/Table';
import { ButtonDefinition } from './definitions/Button';
import { TextDefinition } from './definitions/Text';
import { SelectDefinition } from './definitions/Select';

const REGISTRY = new Map<string, ComponentDefinition<any>>();

function register(def: ComponentDefinition<any>) {
  if (REGISTRY.has(def.type)) {
    console.warn(`Component type "${def.type}" already registered. Overwriting.`);
  }
  REGISTRY.set(def.type, def);
}

// Register all built-in components
register(TextInputDefinition);
register(TableDefinition);
register(ButtonDefinition);
register(TextDefinition);
register(SelectDefinition);

export function getComponentDefinition(type: string): ComponentDefinition {
  const def = REGISTRY.get(type);
  if (!def) throw new Error(`Unknown component type: "${type}"`);
  return def;
}

export function getAllDefinitions(): ComponentDefinition[] {
  return Array.from(REGISTRY.values());
}

export function getDefinitionsByCategory(category: string): ComponentDefinition[] {
  return getAllDefinitions().filter(d => d.meta.category === category);
}
```

### New Store Actions (additions to appStore.ts)

```ts
// Component methods storage
componentMethods: Record<string, Record<string, (...args: any[]) => void>>,

registerComponentMethods: (id: string, methods: Record<string, Function>) => void,
unregisterComponentMethods: (id: string) => void,
callComponentMethod: (id: string, method: string, ...args: any[]) => void,

// Event action execution — generic, uses ActionRegistry (no switch-case)
executeEventActions: (actions: AppEvent[], context?: { $event?: Record<string, unknown> }) => void,

// Undo/redo
history: { components: AppComponent[]; queries: AppQuery[] }[],
historyIndex: number,
pushHistory: () => void,   // called before each mutation
undo: () => void,
redo: () => void,
```

### Undo/Redo System

Simple snapshot-based approach. Before any component/query mutation, snapshot the current state. Keeps last 50 states.

```ts
// Inside appStore.ts

pushHistory: () => {
  const { components, queries, history, historyIndex } = get();
  const snapshot = {
    components: structuredClone(components),
    queries: structuredClone(queries),
  };
  // Truncate any future states (if we undid then made a new change)
  const newHistory = history.slice(0, historyIndex + 1);
  newHistory.push(snapshot);
  // Keep max 50 entries
  if (newHistory.length > 50) newHistory.shift();
  set({ history: newHistory, historyIndex: newHistory.length - 1 });
},

undo: () => {
  const { historyIndex, history } = get();
  if (historyIndex <= 0) return;
  const prev = history[historyIndex - 1];
  set({
    components: structuredClone(prev.components),
    queries: structuredClone(prev.queries),
    historyIndex: historyIndex - 1,
  });
},

redo: () => {
  const { historyIndex, history } = get();
  if (historyIndex >= history.length - 1) return;
  const next = history[historyIndex + 1];
  set({
    components: structuredClone(next.components),
    queries: structuredClone(next.queries),
    historyIndex: historyIndex + 1,
  });
},

// Wire Ctrl+Z / Ctrl+Shift+Z in AppBuilder.tsx useEffect
```

### Evaluator: Minimal JS (Fast-tracked from Phase 4)

The dot-path evaluator can't handle default mock data like `{{ [{ "id": 1, "name": "Alice" }] }}`. Instead of adding JSON.parse hacks, we fast-track a minimal `new Function()` evaluator from the start:

```ts
// src/features/app-builder/lib/evaluator.ts

export function evaluate(template: string, scope: Record<string, unknown>): unknown {
  if (typeof template !== 'string' || !template.includes('{{')) return template;

  // Single expression: {{ expr }} -> return typed value
  const singleMatch = template.match(/^\{\{\s*([\s\S]+?)\s*\}\}$/);
  if (singleMatch) {
    return evaluateExpression(singleMatch[1], scope);
  }

  // String interpolation: "Hello {{ name }}" -> return string
  return template.replace(/\{\{\s*([\s\S]+?)\s*\}\}/g, (_, expr) => {
    const result = evaluateExpression(expr, scope);
    return result == null ? '' : String(result);
  });
}

function evaluateExpression(code: string, scope: Record<string, unknown>): unknown {
  try {
    const scopeKeys = Object.keys(scope);
    const scopeValues = Object.values(scope);
    const fn = new Function(...scopeKeys, `"use strict"; return (${code})`);
    return fn(...scopeValues);
  } catch {
    return undefined;
  }
}
```

This gives us filters, ternaries, array literals, and method calls from day 1. Security note: this runs client-side only. See Phase 4 security section for multi-tenancy considerations.

---

## Phase 2 Spec: Tree Layout

### Why Flat Grid Fails

The current layout is a flat array of components, each with absolute `{ x, y, w, h }` positions in a single `react-grid-layout` grid. This means:
- No containers (can't group components inside a box)
- No tabs/modals (components that contain other components)
- No forms (a submit button can't know which inputs belong to it)
- No repeaters (render a card for each item in an array)

Retool and Appsmith use a **component tree**:
```
Canvas (root container)
├── Header (Text)
├── UserTable (Table)
├── DetailContainer (Container)
│   ├── NameInput (TextInput)
│   ├── EmailInput (TextInput)
│   └── SaveButton (Button)
├── Tabs
│   ├── Tab: "Posts"
│   │   └── PostsTable (Table)
│   └── Tab: "Settings"
│       └── SettingsForm (Container)
└── DeleteModal (Modal)
    ├── WarningText (Text)
    └── ConfirmButton (Button)
```

### Data Model Change

```ts
interface AppComponent {
  id: string;
  type: string;
  parentId: string | null;     // null = root canvas of current page
  props: Record<string, unknown>;
  position: { x: number; y: number; w: number; h: number };  // relative to parent's grid
  events: Record<string, AppEvent[]>;
}
```

**Migration**: Existing saved apps have no `parentId`. On load, default all components to `parentId: null`. Add a migration function:
```ts
function migrateAppDefinition(def: AppDefinition): AppDefinition {
  return {
    ...def,
    components: def.components.map(c => ({
      ...c,
      parentId: c.parentId ?? null,
    })),
  };
}
```

### Layout Strategy: Nested react-grid-layout

Each container component renders its own `<GridLayout>` instance for its children. The canvas itself is the root container.

```
Canvas (GridLayout instance #1)
├── Table (grid item in #1)
├── Container (grid item in #1, but also renders GridLayout #2 inside itself)
│   ├── TextInput (grid item in #2)
│   └── Button (grid item in #2)
```

- **Drag within same container**: normal `react-grid-layout` behavior
- **Drag between containers**: remove from source grid, add to target grid, update `parentId`
- **Drop indicators**: highlight target container when dragging over it

### Helper: Get Children of a Container

```ts
// Used by GridCanvas and any container renderer
function getChildren(components: AppComponent[], parentId: string | null): AppComponent[] {
  return components.filter(c => c.parentId === parentId);
}
```

### Component Tree in Left Panel

Replace flat list with tree view:
```
▼ Canvas
  ├── text1 (Text)
  ├── table1 (Table)
  ▼ container1 (Container)
  │  ├── textInput1 (Text Input)
  │  └── button1 (Button)
  └── modal1 (Modal)
     └── text2 (Text)
```

Interactions:
- Click to select
- Drag to reorder / reparent
- Right-click context menu (delete, duplicate, wrap in container)

### Container Component Types

| Type | Behavior | isContainer |
|------|----------|-------------|
| Container | Simple box with its own grid, optional title/border | true |
| Tabs | Multiple named tabs, each tab is a container | true |
| Modal | Overlay container, opened via method/event | true |
| Form | Container that collects child input values, has onSubmit | true |
| Drawer | Slide-in panel from edge, opened via method | true |
| Repeater | Renders a template container for each item in an array | true |

These are defined using the same `ComponentDefinition` system — they have `isContainer: true` in their meta, and their renderer includes a nested `<GridLayout>`.

### Container Definition Example

```tsx
export const ContainerDefinition: ComponentDefinition<ContainerProps> = {
  type: 'container',
  meta: {
    displayName: 'Container',
    icon: 'Square',
    category: 'layout',
    defaultSize: { w: 6, h: 4 },
    isContainer: true,
  },
  propSchema: [
    { name: 'title', label: 'Title', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'showBorder', label: 'Show Border', section: 'Style', control: 'switch', defaultValue: true },
    { name: 'padding', label: 'Padding', section: 'Style', control: 'select', defaultValue: 'md',
      options: [{ label: 'None', value: 'none' }, { label: 'Small', value: 'sm' }, { label: 'Medium', value: 'md' }, { label: 'Large', value: 'lg' }] },
  ],
  eventSchema: [],
  exposedState: [],
  methods: [],
  Component: ContainerRenderer,  // renders <GridLayout> for its children
};
```

---

## Phase 3 Spec: App Frame, Pages & Navigation

### Why This Matters

Without page-level layout, every app is a single canvas with all components dumped on it. Retool lets you build apps with:
- A sidebar or top navigation bar
- Multiple pages (Users, Settings, Reports) with shared nav
- Each page is its own canvas with its own component tree
- Global queries that persist across pages

This is what separates "internal tool" from "drag some boxes around."

### Data Model Changes

The `AppDefinition` evolves from a flat bag of components/queries to a structured app:

```ts
interface AppDefinition {
  // --- App Frame ---
  frame: AppFrame;

  // --- Pages ---
  pages: AppPage[];

  // --- Components (all pages) ---
  components: AppComponent[];   // each has a pageId + parentId

  // --- Queries ---
  queries: AppQuery[];          // queries can be global or page-scoped

  // --- Global Variables ---
  globalVariables: GlobalVariable[];  // user-defined variables accessible as {{ globals.name }}

  // --- Global settings ---
  settings: AppSettings;
}

interface AppFrame {
  type: 'sidebar' | 'topnav' | 'blank';
  logo?: string;                    // URL or base64
  title?: string;                   // app title shown in nav
  navWidth?: number;                // sidebar width (default 240)
  navCollapsible?: boolean;         // can sidebar collapse to icons
  navPosition?: 'left' | 'right';  // sidebar position
  primaryColor?: string;            // nav accent color
}

interface AppPage {
  id: string;
  name: string;
  icon?: string;           // lucide icon name
  slug: string;            // URL-friendly: "users", "settings"
  isDefault?: boolean;     // shown on app load
  showInNav?: boolean;     // false = hidden page (accessible via navigate action)
  // Each page is a root canvas — components with pageId === this id and parentId === null
}

interface AppComponent {
  id: string;
  type: string;
  pageId: string;                  // which page this component belongs to
  parentId: string | null;         // null = root canvas of the page
  props: Record<string, unknown>;
  position: { x: number; y: number; w: number; h: number };
  events: Record<string, AppEvent[]>;
}

interface AppQuery {
  id: string;
  name: string;
  type: 'rest' | 'workflow';
  config: Record<string, unknown>;
  runOnLoad: boolean;
  scope: 'global' | 'page';    // global = shared across pages, page = runs on page enter
  pageId?: string;              // only for page-scoped queries
}

interface AppSettings {
  theme?: 'light' | 'dark' | 'system';
  maxWidth?: number;         // canvas max-width (default: unlimited)
  customCSS?: string;        // advanced: custom CSS injection
}
```

### Migration from Phase 2

Existing apps have no `frame`, `pages`, or `pageId`. Migration:

```ts
function migrateToPhase3(def: AppDefinition): AppDefinition {
  // If already has pages, no migration needed
  if (def.pages?.length) return def;

  const defaultPageId = 'page_1';
  return {
    frame: { type: 'blank' },
    pages: [{ id: defaultPageId, name: 'Page 1', slug: 'page-1', isDefault: true, showInNav: true }],
    components: (def.components || []).map(c => ({
      ...c,
      pageId: c.pageId ?? defaultPageId,
      parentId: c.parentId ?? null,
    })),
    queries: (def.queries || []).map(q => ({
      ...q,
      scope: q.scope ?? 'global',
    })),
    settings: def.settings ?? {},
  };
}
```

### App Frame Rendering

The frame wraps the entire app and is NOT a draggable component — it's fixed chrome:

```
┌──────────────────────────────────────────┐
│ Sidebar Frame                            │
├─────────┬────────────────────────────────┤
│         │                                │
│  Logo   │   Page Canvas                  │
│         │   (react-grid-layout)          │
│  ─────  │                                │
│  Users  │   ┌─────┐  ┌───────────────┐   │
│  Posts   │   │Table│  │  Container    │   │
│  Settings│  │     │  │  ┌──┐ ┌──┐   │   │
│         │   │     │  │  │In│ │Bt│   │   │
│         │   └─────┘  │  └──┘ └──┘   │   │
│         │            └───────────────┘   │
│         │                                │
└─────────┴────────────────────────────────┘
```

```tsx
// src/features/app-builder/runtime/AppFrame.tsx

export function AppFrame({ children }: { children: React.ReactNode }) {
  const frame = useAppStore(s => s.frame);
  const pages = useAppStore(s => s.pages);
  const currentPageId = useAppStore(s => s.currentPageId);
  const navigateToPage = useAppStore(s => s.navigateToPage);

  if (frame.type === 'blank') {
    return <>{children}</>;
  }

  if (frame.type === 'sidebar') {
    return (
      <div className="flex h-full">
        <aside className="border-r bg-card flex flex-col" style={{ width: frame.navWidth || 240 }}>
          {/* Logo / App Title */}
          <div className="px-4 py-3 border-b">
            {frame.logo && <img src={frame.logo} className="h-6" />}
            {frame.title && <span className="text-sm font-semibold">{frame.title}</span>}
          </div>

          {/* Navigation Links */}
          <nav className="flex-1 py-2">
            {pages.filter(p => p.showInNav !== false).map(page => (
              <button
                key={page.id}
                onClick={() => navigateToPage(page.id)}
                className={cn(
                  "w-full text-left px-4 py-2 text-sm flex items-center gap-2",
                  page.id === currentPageId ? "bg-accent text-accent-foreground" : "hover:bg-muted"
                )}
              >
                {page.icon && <LucideIcon name={page.icon} size={16} />}
                {page.name}
              </button>
            ))}
          </nav>
        </aside>

        {/* Page content */}
        <main className="flex-1 overflow-auto">
          {children}
        </main>
      </div>
    );
  }

  if (frame.type === 'topnav') {
    return (
      <div className="flex flex-col h-full">
        <header className="border-b bg-card px-4 py-2 flex items-center gap-6">
          <div className="flex items-center gap-2">
            {frame.logo && <img src={frame.logo} className="h-6" />}
            {frame.title && <span className="text-sm font-semibold">{frame.title}</span>}
          </div>
          <nav className="flex items-center gap-1">
            {pages.filter(p => p.showInNav !== false).map(page => (
              <button
                key={page.id}
                onClick={() => navigateToPage(page.id)}
                className={cn(
                  "px-3 py-1.5 text-sm rounded-md",
                  page.id === currentPageId ? "bg-accent text-accent-foreground" : "hover:bg-muted"
                )}
              >
                {page.name}
              </button>
            ))}
          </nav>
        </header>
        <main className="flex-1 overflow-auto">
          {children}
        </main>
      </div>
    );
  }
}
```

### Frame Configuration in Editor

In edit mode, clicking the frame/nav area opens a **Frame Settings** panel (instead of the PropertyPanel):

```tsx
// src/features/app-builder/inspector/FrameSettings.tsx

export function FrameSettings() {
  const frame = useAppStore(s => s.frame);
  const updateFrame = useAppStore(s => s.updateFrame);

  return (
    <div className="h-full bg-card border-l flex flex-col overflow-auto">
      <div className="px-3 py-2 border-b">
        <h3 className="text-xs font-semibold">App Frame</h3>
      </div>

      <CollapsibleSection title="Layout">
        <PropField label="Frame Type">
          <SegmentedControl
            value={frame.type}
            onChange={type => updateFrame({ type })}
            options={[
              { value: 'blank', label: 'None' },
              { value: 'sidebar', label: 'Sidebar' },
              { value: 'topnav', label: 'Top Nav' },
            ]}
          />
        </PropField>
      </CollapsibleSection>

      {frame.type !== 'blank' && (
        <>
          <CollapsibleSection title="Branding">
            <PropField label="App Title">
              <Input value={frame.title} onChange={e => updateFrame({ title: e.target.value })} />
            </PropField>
            <PropField label="Logo URL">
              <Input value={frame.logo} onChange={e => updateFrame({ logo: e.target.value })} />
            </PropField>
          </CollapsibleSection>

          {frame.type === 'sidebar' && (
            <CollapsibleSection title="Sidebar">
              <PropField label="Width">
                <Input type="number" value={frame.navWidth || 240}
                  onChange={e => updateFrame({ navWidth: Number(e.target.value) })} />
              </PropField>
              <PropField label="Collapsible">
                <Switch checked={frame.navCollapsible}
                  onCheckedChange={v => updateFrame({ navCollapsible: v })} />
              </PropField>
              <PropField label="Position">
                <SegmentedControl value={frame.navPosition || 'left'}
                  onChange={v => updateFrame({ navPosition: v })}
                  options={[{ value: 'left', label: 'Left' }, { value: 'right', label: 'Right' }]} />
              </PropField>
            </CollapsibleSection>
          )}
        </>
      )}
    </div>
  );
}
```

### Page Management

Pages are managed in the LeftPanel. The LeftPanel tabs evolve:

**Before**: `UI | Queries`
**After**: `Pages | UI | Queries`

```
Pages Tab:
┌──────────────────────┐
│ + Add Page           │
├──────────────────────┤
│ ► Users       (default) │
│   Posts               │
│   Settings            │
│   User Detail (hidden)│
└──────────────────────┘
```

- Click a page to navigate to it (editor shows that page's canvas)
- Right-click: rename, set as default, toggle nav visibility, delete
- Drag to reorder (reorders nav items)

### Page-Scoped Store Additions

```ts
// New store state
frame: AppFrame,
pages: AppPage[],
currentPageId: string,
settings: AppSettings,

// New actions
updateFrame: (updates: Partial<AppFrame>) => void,
addPage: (name: string) => void,
removePage: (pageId: string) => void,
updatePage: (pageId: string, updates: Partial<AppPage>) => void,
reorderPages: (pageIds: string[]) => void,
navigateToPage: (pageId: string) => void,

// Global variables
globalVariables: GlobalVariable[],
globalVariablesState: Record<string, unknown>,   // runtime values: { selectedUserId: '123', ... }
setGlobalVariable: (name: string, value: unknown) => void,
addGlobalVariable: (variable: GlobalVariable) => void,
removeGlobalVariable: (id: string) => void,

// Updated getters
getCurrentPageComponents: () => AppComponent[],  // components for current page only
getPageComponents: (pageId: string) => AppComponent[],
```

### Event Actions (Registry-Driven)

Instead of a hardcoded union type that grows with every new action, `AppEvent` uses the `ActionDefinition` registry pattern (same approach as components):

```ts
// Stored in component events — generic, extensible
interface AppEvent {
  type: string;                        // key into ActionRegistry: 'runQuery', 'navigate', 'showModal', etc.
  params: Record<string, unknown>;     // action-specific params, driven by ActionDefinition.paramsSchema
}

// Examples:
// { type: 'runQuery',          params: { queryId: 'getUsers' } }
// { type: 'navigate',         params: { pageId: 'page_2' } }
// { type: 'callMethod',       params: { componentId: 'modal1', method: 'open' } }
// { type: 'setValue',          params: { componentId: 'textInput1', value: '{{ $event.row.name }}' } }
// { type: 'showNotification', params: { message: 'Saved!', type: 'success' } }
// { type: 'showModal',        params: { componentId: 'deleteModal' } }
// { type: 'copyToClipboard',  params: { value: '{{ table1.selectedRow.id }}' } }
// { type: 'openUrl',          params: { url: 'https://example.com', newTab: true } }
// { type: 'setGlobalVariable',params: { name: 'selectedUserId', value: '{{ $event.row.id }}' } }
```

The `EventActionBuilder` UI reads `ActionDefinition.paramsSchema` to render the correct form for each action type — no switch-case needed. Adding a new action type (e.g., `showNotification`) is one file: define it, register it, done.

```ts
// src/features/app-builder/actions/registry.ts
const ACTION_REGISTRY = new Map<string, ActionDefinition>();

function registerAction(def: ActionDefinition) {
  ACTION_REGISTRY.set(def.type, def);
}

export function getActionDefinition(type: string) { return ACTION_REGISTRY.get(type)!; }
export function getAllActionDefinitions() { return Array.from(ACTION_REGISTRY.values()); }

// Register built-in actions
registerAction(RunQueryAction);
registerAction(NavigateAction);
registerAction(SetValueAction);
registerAction(CallMethodAction);
registerAction(ShowNotificationAction);
registerAction(ShowModalAction);
registerAction(HideModalAction);
registerAction(CopyToClipboardAction);
registerAction(OpenUrlAction);
registerAction(SetGlobalVariableAction);
```

Execution in the store becomes generic:

```ts
executeEventActions: async (actions: AppEvent[], context) => {
  for (const action of actions) {
    const def = getActionDefinition(action.type);
    // Evaluate any expression params before passing to execute
    const evaluatedParams = evaluateActionParams(action.params, context);
    await def.execute(evaluatedParams, {
      $event: context?.$event,
      getState: useAppStore.getState,
      evaluate: (expr) => evaluate(expr, useAppStore.getState().runtimeState),
    });
  }
},
```

### Editor Layout with Frame

In edit mode, the frame is rendered but with an overlay for editing:

```
┌─────────────────────────────────────────────────────┐
│ AppNavbar (save, mode, undo/redo, page selector)    │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─LeftPanel─┐  ┌─ AppFrame ──────────────────────┐ │
│  │ Pages     │  │ ┌Sidebar──┐  ┌─PageCanvas────┐  │ │
│  │ UI        │  │ │ (click  │  │ Components    │  │ │
│  │ Queries   │  │ │  to     │  │ here          │  │ │
│  │           │  │ │  edit)  │  │               │  │ │
│  │           │  │ │         │  │               │  │ │
│  │           │  │ └─────────┘  └───────────────┘  │ │
│  └───────────┘  └─────────────────────────────────┘ │
│                  ┌─ Bottom Panel ──────────────────┐ │
│                  │ QueryEditor (if query selected) │ │
│                  └─────────────────────────────────┘ │
│  ┌─ Right Panel ─────────────────────────────────────┐
│  │ PropertyPanel / FrameSettings / PageSettings      │
│  └───────────────────────────────────────────────────┘
└──────────────────────────────────────────────────────┘
```

### Backend Changes

The `AppDefinition` JSON blob now includes `frame`, `pages`, and `settings`. No schema change needed — the `definition` column is already a JSON field. Backend is agnostic to the structure.

However, the `/apps/run-query` endpoint needs to handle `pageId` scope:
- Global queries: always available
- Page-scoped queries: only execute when their page is active

---

## Phase 4 Spec: JS Expression Engine

### Current Limitation (if we didn't fast-track in Phase 1)

```ts
// Can only do dot-path lookup:
{{ query1.data }}           // works
{{ table1.selectedRow.id }} // works

// Can't do any of these:
{{ query1.data.length }}                          // fails
{{ query1.data.filter(r => r.active) }}           // fails
{{ textInput1.value || "default" }}               // fails
{{ table1.selectedRow ? table1.selectedRow.name : "N/A" }}  // fails
```

### Solution: Sandboxed JS Evaluation

We fast-track a minimal `new Function()` evaluator in Phase 1 (see above). Phase 4 hardens this:

```ts
function evaluateExpression(expr: string, scope: Record<string, unknown>): unknown {
  const code = expr.trim();

  const scopeKeys = Object.keys(scope);
  const scopeValues = Object.values(scope);

  // Add utility libraries to scope
  const utilKeys = ['_', 'dayjs', 'JSON', 'Math', 'Object', 'Array', 'String', 'Number', 'Boolean'];
  const utilValues = [lodash, dayjs, JSON, Math, Object, Array, String, Number, Boolean];

  const fn = new Function(
    ...scopeKeys, ...utilKeys,
    `"use strict"; return (${code})`
  );
  return fn(...scopeValues, ...utilValues);
}
```

### Security Considerations

**Single-user / trusted authors**: `new Function()` is fine. The app author IS the user.

**Multi-tenant / shared apps**: If user A builds an app and user B runs it, user A's expressions execute in user B's browser. This is XSS. Mitigations:
- **Option A**: Use `quickjs-emscripten` — a WebAssembly QuickJS interpreter. Full JS execution in a sandbox with no DOM/network access. ~200KB overhead.
- **Option B**: Use a restricted parser (jsep + custom evaluator) that only allows safe operations.
- **Option C**: Server-side evaluation only (like n8n's expression engine). Slower but completely sandboxed.

For now (single-user internal tools), `new Function()` is the pragmatic choice. Flag for revisit when multi-tenancy is added.

### Scope Available in Expressions

```ts
const expressionScope = {
  // All component runtime state (flat namespace — {{ textInput1.value }}, {{ query1.data }})
  ...runtimeState,

  // Global variables — user-defined, persist across pages ({{ globals.selectedUserId }})
  globals: globalVariablesState,

  // During event handlers:
  $event: { row: {...}, index: 2 },   // payload from the triggering event

  // Utility libraries
  _: lodash,
  dayjs: dayjs,
  moment: dayjs,  // alias for familiarity

  // App-level
  $page: { id: 'page_1', name: 'Users' },
  $app: { name: 'My App', env: 'production' },
  $url: { params: {}, query: {} },    // URL parameters
  $currentUser: { email: '...', name: '...' },  // from auth context (when auth is added)
};
```

---

## Phase 5 Spec: Reactive Dependency Graph

### Problem

Currently: any `runtimeState` change -> every component re-evaluates all its props. At 50 components with 200 expressions, this is O(n*m) on every keystroke.

### Solution

Build a dependency DAG:

```
1. Parse all {{ }} expressions in all component props
2. Extract referenced identifiers: "query1", "table1", etc. (top-level scope keys)
3. Build graph: { "query1": [table1, text3, chart1], "table1": [text2, query2] }
4. When query1's state changes, only re-evaluate table1, text3, chart1
5. If table1's re-evaluation changes table1's exposed state, cascade to text2 and query2
```

### Implementation

```ts
// src/features/app-builder/runtime/dependencyGraph.ts

interface DepGraph {
  // Maps a state key to the set of component IDs that depend on it
  dependents: Map<string, Set<string>>;
  // Maps a component ID to the set of state keys it depends on
  dependencies: Map<string, Set<string>>;
}

function buildDepGraph(components: AppComponent[]): DepGraph {
  const graph: DepGraph = {
    dependents: new Map(),
    dependencies: new Map(),
  };

  for (const comp of components) {
    const deps = new Set<string>();

    for (const [, value] of Object.entries(comp.props)) {
      if (typeof value === 'string' && value.includes('{{')) {
        // Extract top-level identifiers from the expression
        const refs = extractReferences(value);
        refs.forEach(ref => deps.add(ref));
      }
    }

    graph.dependencies.set(comp.id, deps);
    for (const dep of deps) {
      if (!graph.dependents.has(dep)) graph.dependents.set(dep, new Set());
      graph.dependents.get(dep)!.add(comp.id);
    }
  }

  return graph;
}

function extractReferences(template: string): string[] {
  // Parse {{ expr }} and extract top-level identifiers
  // e.g., "{{ query1.data.filter(r => r.active) }}" -> ["query1"]
  // e.g., "{{ table1.selectedRow.id + textInput1.value }}" -> ["table1", "textInput1"]
  const refs: string[] = [];
  const exprRegex = /\{\{\s*([\s\S]+?)\s*\}\}/g;
  let match;
  while ((match = exprRegex.exec(template))) {
    const identifiers = extractIdentifiers(match[1]);
    refs.push(...identifiers);
  }
  return [...new Set(refs)];
}
```

### Selective Subscription in ComponentWrapper

```tsx
// Instead of subscribing to ALL runtimeState:
const runtimeState = useAppStore(s => s.runtimeState);  // BAD: re-renders on any change

// Subscribe only to the state keys this component references:
const relevantState = useAppStore(
  useCallback((s) => {
    const deps = depGraph.dependencies.get(componentId) || new Set();
    const result: Record<string, unknown> = {};
    for (const key of deps) {
      result[key] = s.runtimeState[key];
    }
    // Also include own state
    result[componentId] = s.runtimeState[componentId];
    return result;
  }, [componentId]),
  shallow  // shallow compare the result object
);
```

### Change Propagation

When a component calls `setState()`:

```ts
setRuntimeValue: (id, key, value) => {
  set(prev => ({
    runtimeState: {
      ...prev.runtimeState,
      [id]: { ...prev.runtimeState[id], [key]: value },
    },
  }));
  // The Zustand subscription in each ComponentWrapper will trigger
  // only for components that depend on `id` (via the selective subscription above)
}
```

This is the same pattern React uses internally, and what Retool calls their "reactive evaluation engine."

---

## Phase 6 Spec: Advanced Components & Polish

### New Components

These become easy once Phase 2 (tree layout) and Phase 3 (app frame) are done:

| Component | Category | isContainer | Key Props |
|-----------|----------|-------------|-----------|
| Container | layout | true | title, showBorder, padding |
| Tabs | layout | true | tabs: { label, id }[] |
| Modal | layout | true | title, size, showCloseButton |
| Drawer | layout | true | title, position (left/right), width |
| Form | layout | true | onSubmit event, resetOnSubmit |
| Repeater | layout | true | data (array), itemTemplate |
| Chart | display | false | type (bar/line/pie), data, xAxis, yAxis |
| Image | display | false | src, alt, objectFit |
| Stat | display | false | value, label, trend, trendDirection |
| DatePicker | input | false | value, format, minDate, maxDate |
| NumberInput | input | false | value, min, max, step |
| Textarea | input | false | value, rows, maxLength |
| Checkbox | input | false | checked, label |
| RadioGroup | input | false | options, value |
| Switch | input | false | checked, label |
| FileUpload | input | false | accept, maxSize, multiple |
| Link | action | false | href, text, newTab |
| IconButton | action | false | icon, variant, size |

### Auto-Save

Debounced auto-save (2 second delay after last change):

```ts
// In appStore.ts
autoSave: debounce(async () => {
  const { appId, appName, getDefinition } = useAppStore.getState();
  if (!appId) return;
  await appsApi.update(appId, { name: appName, definition: getDefinition() });
}, 2000),

// Called from pushHistory (every mutation triggers auto-save)
```

### Deploy / Publish

```ts
interface AppDefinition {
  // ... existing fields ...
  deployedVersion?: {
    definition: AppDefinition;  // snapshot at deploy time
    deployedAt: string;
    deployedBy: string;
  };
}
```

- **Edit mode**: works on draft definition
- **Deploy**: snapshots current definition as `deployedVersion`
- **Published URL**: `/apps/{appId}/view` — renders `deployedVersion` in preview mode, read-only
- **Rollback**: restore from `deployedVersion.definition`

### Theming

```ts
interface AppSettings {
  theme?: 'light' | 'dark' | 'system';
  maxWidth?: number;
  customCSS?: string;
  colors?: {
    primary: string;
    background: string;
    card: string;
    text: string;
    border: string;
    accent: string;
  };
  borderRadius?: 'none' | 'sm' | 'md' | 'lg';
  fontFamily?: string;
}
```

Applied via CSS variables on the app container — inherits from shadcn/ui's theming system.

---

## UX Guide Rails

These features sit on top of the component definition system to make the builder intuitive, not just powerful.

### 1. Instant Gratification — Default Mock Data

When a user drags a Table onto the canvas, it should instantly show data, not a blank box. Achieved via `defaultValue` in propSchema:

```ts
// In TableDefinition
{
  name: 'data',
  label: 'Data Source',
  control: 'expression',
  defaultValue: '{{ [{ "id": 1, "name": "Alice", "status": "Active" }, { "id": 2, "name": "Bob", "status": "Pending" }] }}',
  expectedType: 'array',
}
```

User drops a table -> it shows 2 rows instantly -> they understand "this takes an array of objects."

### 2. ExpressionInput — Live Preview + Autocomplete

The `expression` control type renders a smart input (CodeMirror or lightweight custom), not a plain `<input>`:

```tsx
function ExpressionInput({ value, onChange, expectedType }) {
  const runtimeState = useAppStore(s => s.runtimeState);

  // Evaluate in real-time as user types
  const result = evaluate(value, runtimeState);
  const typeMatch = !expectedType || checkType(result, expectedType);

  return (
    <div className="flex flex-col gap-1">
      <CodeEditor
        value={value}
        onChange={onChange}
        // Feed component/query names for autocomplete
        completions={buildCompletions(runtimeState)}
      />

      {/* Live preview below the input */}
      <div className="text-[10px] font-mono text-muted-foreground bg-muted/50 px-2 py-1 rounded truncate">
        {value.includes('{{') && (
          <>
            {typeMatch ? 'OK' : 'type mismatch — '}
            Evaluates to: {formatPreview(result)}
          </>
        )}
      </div>
    </div>
  );
}
```

User types `{{ qu` -> autocomplete suggests `query1` -> they complete `{{ query1.data }}` -> live preview shows "Array (45 items)." They know it works before looking at the canvas.

Autocomplete source: all `exposedState` from registered components + all query IDs + `$event` (when inside an event handler).

### 3. Query Quick-Bind Dropdown

The `querySelector` control type renders a dropdown of available queries instead of a raw expression input:

```tsx
function QuerySelector({ value, onChange }) {
  const queries = useAppStore(s => s.queries);

  return (
    <Select onValueChange={(qId) => onChange(`{{ ${qId}.data }}`)}>
      <SelectTrigger>{value || 'Select a query...'}</SelectTrigger>
      <SelectContent>
        {queries.map(q => (
          <SelectItem key={q.id} value={q.id}>{q.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

Under the hood, selecting "getUsers" writes `{{ getUsers.data }}` to the prop. Power users can switch to the expression input; beginners use the dropdown.

### 4. Auto-Generate Columns

When a table is connected to data, offer a "Generate Columns" button that introspects the first row:

```tsx
function handleAutoGenerate(data: Record<string, unknown>[]) {
  if (!data.length) return;
  const columns = Object.keys(data[0]).map(key => ({
    field: key,
    headerName: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
    type: typeof data[0][key] === 'number' ? 'number' : 'string',
    width: 150,
  }));
  updateProps(componentId, { columns });
}
```

### 5. State Reference Panel

The PropertyPanel's "State Reference" section (from `exposedState`) shows copyable `{{ component.field }}` expressions. Click to copy. Users never have to guess what's available.

### 6. Component Quick Actions

When hovering a component on the canvas (edit mode):
- **Delete** (trash icon)
- **Duplicate** (copy icon)
- **Wrap in Container** (box icon) — wraps the component in a new Container
- **Lock/Unlock** — prevents accidental drag

### 7. Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Z | Undo |
| Ctrl+Shift+Z | Redo |
| Ctrl+S | Save |
| Delete/Backspace | Delete selected component |
| Ctrl+D | Duplicate selected component |
| Ctrl+C / Ctrl+V | Copy / Paste component |
| Escape | Deselect |
| Tab | Cycle through components |

---

## Implementation Gotchas

### 1. Performance: runtimeState selector causes global re-renders
`useAppStore(s => s.runtimeState)` in ComponentWrapper means every component re-renders when ANY state changes (e.g., typing in one input re-renders all 20 components). For Phase 1 this is acceptable with 5-15 components. For Phase 5 (reactive DAG), replace with selective subscriptions. In the meantime, mitigate with `useShallow` or a custom equality check that only triggers when the specific keys this component references have changed.

### 2. Component method cleanup on unmount
`registerMethods` in `useEffect` without cleanup leaks methods in Zustand when components are deleted. Handled in ComponentWrapper with `unregisterComponentMethods(id)` in cleanup.

### 3. Stable callback identities
ComponentWrapper must memoize `handleSetState`, `handleFireEvent`, `handleRegisterMethods` with `useCallback` + `getState()` pattern. Without this, every runtimeState change creates new callback identities, causing every renderer to re-render even if its own props haven't changed.

### 4. Controlled vs. uncontrolled renderers
Renderers should NOT maintain local `useState` for values that are also in runtimeState. This causes state drift (e.g., an expression-driven `defaultValue` changes but local state doesn't update). Instead, renderers are controlled: they read values from props (which come from runtimeState via ComponentWrapper) and call `setState()` to update.

### 5. Nested grid layout z-index and drag conflicts
When nesting `react-grid-layout` instances (Phase 2), drag events can bubble from child grid to parent grid. Solution: stop propagation on child grid drag handles, use `isDragging` state to disable parent grid while child is being dragged.

### 6. Page transition and query lifecycle
When navigating between pages (Phase 3), page-scoped queries should re-run on page enter, but global queries should persist. Component runtimeState for non-visible pages should be preserved (not cleared), so navigating back shows previous state.

### 7. Backend definition migration
The backend stores `definition` as a JSON blob. Each phase changes the shape. Add a `version` field to `AppDefinition` and a migration chain:
```ts
interface AppDefinition {
  version: 1 | 2 | 3;  // incremented per phase
  // ...
}

function migrateDefinition(def: AppDefinition): AppDefinition {
  if (!def.version || def.version < 2) def = migrateV1toV2(def);  // add parentId
  if (def.version < 3) def = migrateV2toV3(def);                   // add frame, pages
  return def;
}
```

Run migration on load (frontend), never modify stored data in-place.

---

## Deliverables by Phase

### Phase 1 Deliverable
1. **Type definitions**: `ComponentDefinition`, `PropField`, `EventField`, `StateField`, `MethodField`, `RendererProps`, `UniversalProps`, `ActionDefinition`, `ActionParamField`, `GlobalVariable`
2. **ComponentWrapper**: expression evaluation, hidden/disabled, event dispatch, method registration, stable callbacks, controlled pattern
3. **Generic PropertyPanel**: schema-driven, no switch-case, collapsible sections, event wiring UI, state reference
4. **PropControl**: renders `text`, `expression`, `switch`, `select`, `querySelector`, `json` controls
5. **ExpressionInput**: with live preview (autocomplete can be basic initially)
6. **5 component definitions**: Table, Button, TextInput, Text, Select — migrated to new pattern
7. **Component registry**: `register()` + `getComponentDefinition()` + `getAllDefinitions()`
8. **Action registry**: `registerAction()` + `getActionDefinition()` + `getAllActionDefinitions()` + built-in actions (runQuery, setValue, navigate, callMethod)
9. **Store additions**: `registerComponentMethods`, `unregisterComponentMethods`, `callComponentMethod`, `executeEventActions` (generic, registry-driven)
10. **Undo/redo**: snapshot-based history with Ctrl+Z/Ctrl+Shift+Z
11. **Evaluator upgrade**: minimal `new Function()` evaluator (replaces dot-path)
12. **Migration**: delete `componentRegistry.ts`, `renderers/` directory, PropertyPanel switch-case. Replace GridCanvas to use ComponentWrapper.

### Phase 2 Deliverable
1. `parentId` on AppComponent
2. Nested `react-grid-layout` rendering
3. Container, Tabs, Modal component definitions
4. Component tree view in LeftPanel
5. Drag between containers
6. Migration function for existing apps

### Phase 3 Deliverable
1. `AppFrame` type and rendering (sidebar, topnav, blank)
2. `AppPage` type and multi-page support
3. `pageId` on AppComponent
4. Page management UI in LeftPanel
5. Frame settings panel
6. Additional action definitions (showModal, hideModal, showNotification, copyToClipboard, openUrl)
7. Global variables: definition, runtime state, `setGlobalVariable` action, `{{ globals.x }}` in expressions
8. Page-scoped vs global queries
9. Migration function for existing apps
10. Definition versioning

### Phase 4 Deliverable
1. Hardened JS evaluator with utility libraries (lodash, dayjs)
2. Security assessment for multi-tenancy
3. `$page`, `$app`, `$url`, `$currentUser` scope variables
4. Error boundaries per-expression (graceful failures)

### Phase 5 Deliverable
1. Dependency graph builder
2. Selective Zustand subscriptions per component
3. Change propagation with topological ordering
4. Performance benchmarking (target: 100+ components, <16ms keystroke response)

### Phase 6 Deliverable
1. Advanced components (see table above)
2. Auto-save
3. Deploy/publish with versioning
4. Theming system
5. Custom CSS support

### File Structure After Phase 3

```
src/features/app-builder/
├── definitions/                    # One file per component
│   ├── Table.tsx
│   ├── Button.tsx
│   ├── TextInput.tsx
│   ├── Text.tsx
│   ├── Select.tsx
│   ├── Container.tsx              # Phase 2
│   ├── Tabs.tsx                   # Phase 2
│   └── Modal.tsx                  # Phase 2
├── runtime/
│   ├── ComponentWrapper.tsx        # Universal wrapper
│   ├── ExpressionInput.tsx         # Smart {{ }} input with live preview
│   └── AppFrame.tsx                # Phase 3: sidebar/topnav/blank frame
├── inspector/
│   ├── PropertyPanel.tsx           # Generic, schema-driven
│   ├── PropControl.tsx             # Control type switch
│   ├── EventActionBuilder.tsx      # Event wiring UI
│   ├── QuerySelector.tsx           # Query quick-bind dropdown
│   ├── FrameSettings.tsx           # Phase 3: frame configuration
│   └── PageSettings.tsx            # Phase 3: page properties
├── actions/                         # One file per action type (same registry pattern)
│   ├── registry.ts                 # Action registration
│   ├── RunQuery.ts
│   ├── Navigate.ts
│   ├── SetValue.ts
│   ├── CallMethod.ts
│   ├── ShowNotification.ts         # Phase 3
│   ├── ShowModal.ts                # Phase 3
│   ├── CopyToClipboard.ts         # Phase 3
│   └── OpenUrl.ts                  # Phase 3
├── registry.ts                     # Component registration
├── types.ts                        # All type definitions
├── stores/
│   └── appStore.ts                 # Full app state (components, queries, frame, pages, history)
├── lib/
│   ├── evaluator.ts                # JS expression evaluator
│   ├── migration.ts                # Definition version migration chain
│   └── depGraph.ts                 # Phase 5: dependency graph
├── components/
│   ├── AppBuilder.tsx              # Main layout shell
│   ├── AppNavbar.tsx               # Top bar (save, mode, undo/redo)
│   ├── GridCanvas.tsx              # Uses ComponentWrapper, supports nesting (Phase 2)
│   ├── LeftPanel.tsx               # Pages + UI tree + Queries
│   └── QueryEditor.tsx             # REST/Workflow query config
```
