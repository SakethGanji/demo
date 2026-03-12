import { createContext, useContext, type FC } from 'react'

// --- Node Model ---

// --- Breakpoints ---

export type BreakpointId = 'desktop' | 'tablet' | 'mobile'

export interface BreakpointConfig {
  id: BreakpointId
  label: string
  maxWidth: number | null // null = no limit (desktop)
  icon: string
}

export const BREAKPOINTS: BreakpointConfig[] = [
  { id: 'desktop', label: 'Desktop', maxWidth: null, icon: 'Monitor' },
  { id: 'tablet', label: 'Tablet', maxWidth: 768, icon: 'Tablet' },
  { id: 'mobile', label: 'Mobile', maxWidth: 375, icon: 'Smartphone' },
]

// --- Element States ---

export type ElementState = 'default' | 'hover' | 'focus' | 'active'

// --- Style Classes ---

export interface StyleClass {
  id: string
  name: string
  styles: Record<string, unknown>
}

export interface AppNode {
  id: string
  type: string
  parentId: string | null
  childIds: string[]
  linkedNodes: Record<string, string>
  props: Record<string, unknown>
  isCanvas: boolean
  hidden: boolean
  /** Per-breakpoint prop overrides (desktop is base, tablet/mobile override) */
  breakpointOverrides?: Partial<Record<BreakpointId, Record<string, unknown>>>
  /** Per-element-state style overrides */
  stateStyles?: Partial<Record<ElementState, Record<string, unknown>>>
  /** Applied style class IDs in order of precedence */
  classIds?: string[]
}

// --- Component Definition System ---

export type PropControlType =
  | 'text'
  | 'number'
  | 'switch'
  | 'select'
  | 'color'
  | 'expression'
  | 'icon'

export interface PropField {
  name: string
  label: string
  section: string
  control: PropControlType
  defaultValue: unknown
  options?: { label: string; value: string }[]
  /** Minimum value for number controls */
  min?: number
  /** Maximum value for number controls */
  max?: number
  /** Whether this prop is required (prevents empty values) */
  required?: boolean
  /** Regex pattern for text validation */
  validationPattern?: string
}

export interface EventField {
  name: string
  label: string
}

export interface StateField {
  name: string
  label: string
  defaultValue: unknown
}

export interface RendererProps<TProps = Record<string, unknown>> {
  id: string
  props: TProps
  children?: React.ReactNode
  onEvent?: (name: string, payload?: unknown) => void
}

export interface NodeRules {
  canDrag?: (node: AppNode) => boolean
  canMoveIn?: (incomingNode: AppNode, currentNode: AppNode) => boolean
  canMoveOut?: (outgoingNode: AppNode, currentNode: AppNode) => boolean
}

export type ComponentCategory = 'layout' | 'content' | 'input' | 'data' | 'feedback' | 'navigation'

export type ComponentTier = 'component' | 'template' | 'layout'

export interface ComponentMeta {
  displayName: string
  icon: string
  category: ComponentCategory
  defaultProps: Record<string, unknown>
  isContainer?: boolean
  /** Which palette tab this component appears in. Defaults to 'component'. */
  tier?: ComponentTier
  /** Auto-create child nodes when this component is added (e.g. Tabs creates panel containers) */
  defaultChildren?: Array<{ type: string; props?: Record<string, unknown> }>
  /** Extra styles applied to the NodeWrapper div (e.g. flex:1 for full-height components) */
  wrapperStyle?: React.CSSProperties
}

export interface ComponentDefinition<TProps = Record<string, unknown>> {
  type: string
  meta: ComponentMeta
  propSchema: PropField[]
  eventSchema: EventField[]
  exposedState: StateField[]
  Component: FC<RendererProps<TProps>>
  SettingsPanel?: FC<{ nodeId: string }>
  rules?: NodeRules
}

/**
 * Helper to define a component with full type inference.
 * Eliminates the need for `as any` casts on Component/registration.
 *
 * Also merges propSchema defaultValues into meta.defaultProps,
 * so you only need to declare defaults in one place (meta.defaultProps wins on conflicts).
 */
export function defineComponent<TProps extends Record<string, unknown>>(
  def: ComponentDefinition<TProps>
): ComponentDefinition {
  // Merge: propSchema defaults serve as fallback, meta.defaultProps takes priority
  const schemaDefaults: Record<string, unknown> = {}
  for (const field of def.propSchema) {
    if (field.defaultValue !== undefined) {
      schemaDefaults[field.name] = field.defaultValue
    }
  }
  def.meta.defaultProps = { ...schemaDefaults, ...def.meta.defaultProps }
  return def as ComponentDefinition
}

// --- Runtime ---

export interface StoreDefinition {
  id: string
  name: string
  initialValue: unknown
}

export interface WebhookDefinition {
  id: string
  name: string
  url: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  headers: Record<string, string>
  body: string
  /** Optional transform steps applied to the response before storing */
  steps: TransformStep[]
}

export type EventAction =
  | { type: 'setState'; storeId: string; value: string }
  | { type: 'mergeState'; storeId: string; value: string }
  | { type: 'setProperty'; storeId: string; path: string; value: string }
  | { type: 'appendArray'; storeId: string; path: string; value: string }
  | { type: 'removeArrayIndex'; storeId: string; path: string; index: string }
  | { type: 'setComponentState'; nodeId: string; property: string; value: string }
  | { type: 'runWebhook'; webhookId: string; resultStore: string; alertStore?: string }
  | { type: 'alert'; message: string }
  | { type: 'pushAlert'; store: string; message: string; variant: 'success' | 'error' | 'warning' | 'info' }
  | { type: 'condition'; expression: string; thenActions: EventAction[]; elseActions: EventAction[] }

export interface EventHandlerConfig {
  event: string
  actions: EventAction[]
}

// --- Transforms (used inline within API query definitions) ---

export type FilterOp = 'eq' | 'neq' | 'gt' | 'lt' | 'gte' | 'lte' | 'contains' | 'startsWith' | 'exists'

export type TransformStep =
  | { type: 'pick'; path: string }
  | { type: 'filter'; field: string; op: FilterOp; value: string }
  | { type: 'sort'; field: string; direction: 'asc' | 'desc' }
  | { type: 'map'; fields: string }
  | { type: 'slice'; start: string; end: string }
  | { type: 'find'; field: string; op: FilterOp; value: string }
  | { type: 'count' }
  | { type: 'expression'; expr: string }

// --- DnD ---

export type DragSource =
  | { type: 'new'; componentType: string }
  | { type: 'move'; nodeId: string }

export interface DropIndicator {
  parentId: string
  index: number
}

// --- Component Registry ---

const registry = new Map<string, ComponentDefinition>()

export function registerComponent(def: ComponentDefinition) {
  if (registry.has(def.type)) {
    console.warn(`[app-builder] Component "${def.type}" registered twice — overwriting.`)
  }
  registry.set(def.type, def)
}

export function getDefinition(type: string): ComponentDefinition | undefined {
  return registry.get(type)
}

export function getAllDefinitions(): ComponentDefinition[] {
  return Array.from(registry.values())
}

// --- Repeat Context ---

export interface RepeatScope {
  /** Current item data */
  item: unknown
  /** Current iteration index (0-based) */
  index: number
  /** The full array being iterated */
  items: unknown[]
}

/**
 * Context for passing repeat scope (item/index) from a List component
 * down to its children. Nested Lists shadow the outer context automatically.
 */
export const RepeatContext = createContext<RepeatScope | null>(null)

export function useRepeatScope(): RepeatScope | null {
  return useContext(RepeatContext)
}

// --- Constants ---

export const shadowMap: Record<string, string> = {
  '0': 'none',
  '1': 'var(--shadow-sm, 0 1px 3px 0 rgba(0,0,0,0.1))',
  '2': 'var(--shadow-md, 0 4px 6px -1px rgba(0,0,0,0.1))',
  '3': 'var(--shadow-lg, 0 10px 15px -3px rgba(0,0,0,0.1))',
}

// --- Templates ---

/**
 * A template is a recipe for creating a subtree of nodes + stores.
 * When the user clicks a template, it inserts the full tree into
 * the document at the selected position.
 */
export interface AppTemplate {
  id: string
  name: string
  description: string
  icon: string
  /** Nodes to create. Use placeholder IDs — they get remapped on insert. */
  nodes: Record<string, Omit<AppNode, 'id'> & { id: string }>
  /** Which node is the root of this template */
  rootId: string
  /** Stores to create alongside the template */
  stores?: StoreDefinition[]
  /** Webhooks (API calls) to create alongside the template */
  webhooks?: WebhookDefinition[]
}

export const templates: AppTemplate[] = [
  {
    id: 'chatbot',
    name: 'Chatbot',
    description: 'Chat interface with message list, input field, and send button. Includes a messages store.',
    icon: 'MessageSquare',
    rootId: 'chat_root',
    stores: [
      {
        id: 'store_messages',
        name: 'messages',
        initialValue: [
          { role: 'assistant', content: 'Hello! How can I help you today?' },
        ],
      },
      {
        id: 'store_chat_response',
        name: 'chatResponse',
        initialValue: null,
      },
    ],
    webhooks: [
      {
        id: 'webhook_chat_llm',
        name: 'Chat LLM',
        url: 'http://localhost:8000/webhook/p/chat',
        method: 'POST' as const,
        headers: { 'Content-Type': 'application/json' },
        body: '{{ {"message": stores.messages.filter(m => m.role === "user").slice(-1)[0]?.content} }}',
        steps: [
          { type: 'pick', path: 'data.0' },
        ],
      },
    ],
    nodes: {
      chat_root: {
        id: 'chat_root',
        type: 'Container',
        parentId: null,
        childIds: ['chat_log', 'chat_input_bar'],
        linkedNodes: {},
        props: {
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          gap: '0',
          paddingTop: '0',
          paddingRight: '0',
          paddingBottom: '0',
          paddingLeft: '0',
          overflow: 'hidden',
          background: 'transparent',
          borderRadius: '0',
          borderWidth: '0',
          borderColor: '',
          borderStyle: 'none',
          alignItems: 'stretch',
          justifyContent: 'flex-start',
          fillSpace: 'yes',
          position: 'static',
          opacity: '100',
          shadow: '0',
          cursor: 'default',
          customStyles: '',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_log: {
        id: 'chat_log',
        type: 'ScrollArea',
        parentId: 'chat_root',
        childIds: ['chat_list'],
        linkedNodes: {},
        props: {
          flexDirection: 'column',
          alignItems: 'stretch',
          justifyContent: 'flex-start',
          fillSpace: 'yes',
          paddingTop: '16',
          paddingRight: '24',
          paddingBottom: '16',
          paddingLeft: '24',
          gap: '0',
          overflow: 'auto',
          maxHeight: '',
          minHeight: '0',
          background: 'transparent',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_list: {
        id: 'chat_list',
        type: 'List',
        parentId: 'chat_log',
        childIds: ['chat_msg_row'],
        linkedNodes: {},
        props: {
          data: '{{ stores.messages }}',
          direction: 'column',
          gap: '12',
          alignItems: 'stretch',
          emptyText: 'No messages yet',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_msg_row: {
        id: 'chat_msg_row',
        type: 'Container',
        parentId: 'chat_list',
        childIds: ['chat_bubble'],
        linkedNodes: {},
        props: {
          display: 'flex',
          flexDirection: 'row',
          justifyContent: "{{ item.role === 'user' ? 'flex-end' : 'flex-start' }}",
          alignItems: 'flex-start',
          gap: '0',
          paddingTop: '2',
          paddingRight: '0',
          paddingBottom: '2',
          paddingLeft: '0',
          background: 'transparent',
          borderRadius: '0',
          borderWidth: '0',
          borderColor: '',
          borderStyle: 'none',
          overflow: 'visible',
          fillSpace: 'no',
          position: 'static',
          opacity: '100',
          shadow: '0',
          cursor: 'default',
          customStyles: '',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_bubble: {
        id: 'chat_bubble',
        type: 'Card',
        parentId: 'chat_msg_row',
        childIds: ['chat_msg_text'],
        linkedNodes: {},
        props: {
          flexDirection: 'column',
          gap: '0',
          paddingTop: '10',
          paddingRight: '14',
          paddingBottom: '10',
          paddingLeft: '14',
          background: "{{ item.role === 'user' ? '#3b82f6' : '#f3f4f6' }}",
          color: "{{ item.role === 'user' ? '#ffffff' : 'var(--foreground, #1a1a1a)' }}",
          borderRadius: '18',
          borderWidth: '0',
          borderColor: '',
          borderStyle: 'none',
          overflow: 'hidden',
          fillSpace: 'no',
          position: 'static',
          opacity: '100',
          shadow: '0',
          cursor: 'default',
          alignItems: 'stretch',
          justifyContent: 'flex-start',
          maxWidth: '75%',
          customStyles: '{"width":"fit-content"}',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_msg_text: {
        id: 'chat_msg_text',
        type: 'Text',
        parentId: 'chat_bubble',
        childIds: [],
        linkedNodes: {},
        props: {
          content: '{{ item.content }}',
          format: 'markdown',
          fontSize: '14',
          fontWeight: '400',
          color: 'inherit',
          textAlign: 'left',
          lineHeight: '1.5',
          letterSpacing: '',
          textTransform: '',
          opacity: '100',
          maxWidth: '',
        },
        isCanvas: false,
        hidden: false,
      },
      chat_input_bar: {
        id: 'chat_input_bar',
        type: 'Container',
        parentId: 'chat_root',
        childIds: ['chat_input_wrap'],
        linkedNodes: {},
        props: {
          display: 'flex',
          flexDirection: 'column',
          gap: '0',
          paddingTop: '12',
          paddingRight: '24',
          paddingBottom: '20',
          paddingLeft: '24',
          background: 'transparent',
          alignItems: 'stretch',
          justifyContent: 'center',
          borderStyle: 'none',
          borderWidth: '0',
          borderColor: '',
          overflow: 'visible',
          position: 'static',
          fillSpace: 'no',
          opacity: '100',
          borderRadius: '0',
          shadow: '0',
          cursor: 'default',
          customStyles: '',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_input_wrap: {
        id: 'chat_input_wrap',
        type: 'Container',
        parentId: 'chat_input_bar',
        childIds: ['chat_input', 'chat_send'],
        linkedNodes: {},
        props: {
          display: 'flex',
          flexDirection: 'row',
          gap: '8',
          paddingTop: '8',
          paddingRight: '12',
          paddingBottom: '8',
          paddingLeft: '12',
          background: 'var(--muted, #f3f4f6)',
          alignItems: 'center',
          justifyContent: 'flex-start',
          borderStyle: 'solid',
          borderWidth: '1',
          borderColor: 'var(--border)',
          overflow: 'visible',
          position: 'static',
          fillSpace: 'no',
          opacity: '100',
          borderRadius: '24',
          shadow: '1',
          cursor: 'default',
          customStyles: '',
        },
        isCanvas: true,
        hidden: false,
      },
      chat_input: {
        id: 'chat_input',
        type: 'Input',
        parentId: 'chat_input_wrap',
        childIds: [],
        linkedNodes: {},
        props: {
          placeholder: 'Message...',
          fullWidth: true,
          fillSpace: 'yes',
          __eventHandlers: [
            {
              event: 'onSubmit',
              actions: [
                {
                  type: 'appendArray',
                  storeId: 'messages',
                  path: '',
                  value: '{{ {"role": "user", "content": components.chat_input.value} }}',
                },
                {
                  type: 'setComponentState',
                  nodeId: 'chat_input',
                  property: 'value',
                  value: '',
                },
                {
                  type: 'appendArray',
                  storeId: 'messages',
                  path: '',
                  value: '{{ {"role": "assistant", "content": "..."} }}',
                },
                {
                  type: 'runWebhook',
                  webhookId: 'webhook_chat_llm',
                  resultStore: 'chatResponse',
                },
                {
                  type: 'setProperty',
                  storeId: 'messages',
                  path: '{{ (stores.messages.length - 1) + ".content" }}',
                  value: '{{ stores.chatResponse.response }}',
                },
              ],
            },
          ],
        },
        isCanvas: false,
        hidden: false,
      },
      chat_send: {
        id: 'chat_send',
        type: 'Button',
        parentId: 'chat_input_wrap',
        childIds: [],
        linkedNodes: {},
        props: {
          label: '',
          icon: 'send',
          variant: 'default',
          size: 'sm',
          __eventHandlers: [
            {
              event: 'onClick',
              actions: [
                {
                  type: 'appendArray',
                  storeId: 'messages',
                  path: '',
                  value: '{{ {"role": "user", "content": components.chat_input.value} }}',
                },
                {
                  type: 'setComponentState',
                  nodeId: 'chat_input',
                  property: 'value',
                  value: '',
                },
                {
                  type: 'appendArray',
                  storeId: 'messages',
                  path: '',
                  value: '{{ {"role": "assistant", "content": "..."} }}',
                },
                {
                  type: 'runWebhook',
                  webhookId: 'webhook_chat_llm',
                  resultStore: 'chatResponse',
                },
                {
                  type: 'setProperty',
                  storeId: 'messages',
                  path: '{{ (stores.messages.length - 1) + ".content" }}',
                  value: '{{ stores.chatResponse.response }}',
                },
              ],
            },
          ],
        },
        isCanvas: false,
        hidden: false,
      },
    },
  },

  // ── Form Template ─────────────────────────────────────────────────────────
  {
    id: 'form',
    name: 'Form',
    description: 'Simple form with text input, textarea, and submit button that calls an API endpoint.',
    icon: 'FileText',
    rootId: 'form_root',
    stores: [
      {
        id: 'store_form_result',
        name: 'formResult',
        initialValue: null,
      },
    ],
    webhooks: [
      {
        id: 'webhook_submit_form',
        name: 'Submit Form',
        url: 'https://httpbin.org/post',
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{{ {"name": components.form_name.value, "message": components.form_message.value} }}',
        steps: [],
      },
    ],
    nodes: {
      form_root: {
        id: 'form_root',
        type: 'Container',
        parentId: null,
        childIds: ['form_heading', 'form_name_label', 'form_name', 'form_message_label', 'form_message', 'form_submit', 'form_result_text'],
        linkedNodes: {},
        props: {
          display: 'flex',
          flexDirection: 'column',
          gap: '12',
          paddingTop: '24',
          paddingRight: '24',
          paddingBottom: '24',
          paddingLeft: '24',
          background: 'transparent',
          borderRadius: '8',
          borderWidth: '1',
          borderColor: 'var(--border)',
          borderStyle: 'solid',
          alignItems: 'stretch',
          justifyContent: 'flex-start',
          overflow: 'visible',
          fillSpace: 'no',
          position: 'static',
          opacity: '100',
          shadow: '1',
          cursor: 'default',
          customStyles: '',
        },
        isCanvas: true,
        hidden: false,
      },
      form_heading: {
        id: 'form_heading',
        type: 'Heading',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          text: 'Contact Us',
          level: 'h2',
        },
        isCanvas: false,
        hidden: false,
      },
      form_name_label: {
        id: 'form_name_label',
        type: 'Label',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          text: 'Name',
        },
        isCanvas: false,
        hidden: false,
      },
      form_name: {
        id: 'form_name',
        type: 'Input',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          placeholder: 'Enter your name',
          fullWidth: true,
        },
        isCanvas: false,
        hidden: false,
      },
      form_message_label: {
        id: 'form_message_label',
        type: 'Label',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          text: 'Message',
        },
        isCanvas: false,
        hidden: false,
      },
      form_message: {
        id: 'form_message',
        type: 'Textarea',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          placeholder: 'Write your message...',
          rows: 4,
          fullWidth: true,
        },
        isCanvas: false,
        hidden: false,
      },
      form_submit: {
        id: 'form_submit',
        type: 'Button',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          label: 'Submit',
          variant: 'default',
          size: 'default',
          fullWidth: true,
          __eventHandlers: [
            {
              event: 'onClick',
              actions: [
                {
                  type: 'runWebhook',
                  webhookId: 'webhook_submit_form',
                  resultStore: 'formResult',
                },
              ],
            },
          ],
        },
        isCanvas: false,
        hidden: false,
      },
      form_result_text: {
        id: 'form_result_text',
        type: 'Text',
        parentId: 'form_root',
        childIds: [],
        linkedNodes: {},
        props: {
          text: '{{ stores.formResult ? "Submitted successfully!" : "" }}',
          fontSize: '13',
          color: 'var(--success, #22c55e)',
        },
        isCanvas: false,
        hidden: false,
      },
    },
  },
]
