/**
 * The hand-written kit manifest that drove the props panel before the derived registry (registry/extract.ts). Kept as a
 * test fixture: the registry must reproduce it (registry.test.ts), apart from the listed, deliberate upgrades.
 */
import type { ListItem, PropEditor } from '../manifest'

export interface KitComponent {
  props: Record<string, PropEditor>
  children?: 'text' | 'slot' | 'none'
  source: 'ui' | 'DataTable' | 'resource' | 'review'
}

export const TONES = ['gray', 'blue', 'green', 'red', 'amber', 'purple', 'cyan', 'rose'] as const
export const COLUMN_FORMATS = ['text', 'date', 'datetime', 'money', 'number', 'badge', 'bool', 'ago'] as const

const text = { kind: 'text' } as const
const bound = { kind: 'bound' } as const
const event = { kind: 'event' } as const
const slot = { kind: 'slot' } as const
const fieldNames: PropEditor = { kind: 'list', item: { kind: 'string', noun: 'field' } }

const COLUMN: ListItem = {
  kind: 'object',
  noun: 'column',
  label: 'header',
  fields: {
    header: text,
    key: text,
    format: { kind: 'enum', options: COLUMN_FORMATS },
    align: { kind: 'enum', options: ['left', 'right'] },
    width: text,
    sortable: { kind: 'bool' },
    mobile: { kind: 'enum', options: ['primary', 'meta', 'secondary', 'hidden'] },
    hidden: { kind: 'bool' },
  },
  template: { key: '$', header: '$' },
  hideable: true,
}

const TILE: ListItem = {
  kind: 'object',
  noun: 'tile',
  label: 'label',
  fields: { label: text, filter: { kind: 'map' }, sum: text, tone: { kind: 'enum', options: ['good', 'warn', 'bad', 'none'] }, hidden: { kind: 'bool' } },
  template: { label: '$' },
  hideable: true,
}

const TAB: ListItem = {
  kind: 'object',
  noun: 'tab',
  label: 'label',
  fields: { label: text, id: text, hidden: { kind: 'bool' } },
  template: { id: '$', label: '$' },
  hideable: true,
}

const SCREEN: ListItem = {
  kind: 'object',
  noun: 'screen',
  label: 'label',
  fields: { label: text, id: text, roles: { kind: 'tags' }, hidden: { kind: 'bool' } },
  // a new screen needs a render function; adding one is an AI / code edit
  template: {},
  hideable: true,
}

const pageProps = { title: text, subtitle: text, actions: slot } as const

export const KIT_MANIFEST: Record<string, KitComponent> = {
  Button: {
    source: 'ui',
    children: 'text',
    props: {
      variant: { kind: 'enum', options: ['primary', 'secondary', 'ghost', 'danger', 'success'], default: 'secondary' },
      size: { kind: 'enum', options: ['sm', 'md'], default: 'md' },
      loading: bound,
      disabled: bound,
      onClick: event,
      title: text,
    },
  },
  Card: { source: 'ui', children: 'slot', props: { title: text, subtitle: text, actions: slot } },
  Tag: { source: 'ui', children: 'text', props: { tone: { kind: 'enum', options: TONES, default: 'gray' } } },
  StatusBadge: { source: 'ui', props: { status: bound, label: text, detail: text } },
  Tabs: { source: 'ui', props: { tabs: { kind: 'list', item: TAB }, value: bound, onChange: event } },
  Drawer: {
    source: 'ui',
    children: 'slot',
    props: {
      title: text,
      subtitle: text,
      width: { kind: 'enum', options: ['md', 'lg', 'xl'], default: 'lg' },
      tone: { kind: 'enum', options: ['none', 'red', 'amber', 'blue', 'green'], default: 'none' },
      variant: { kind: 'enum', options: ['overlay', 'docked'], default: 'overlay' },
      open: bound,
      onClose: event,
      actions: slot,
      footer: slot,
    },
  },
  EmptyState: { source: 'ui', props: { title: text, hint: text, action: slot } },
  Stat: {
    source: 'ui',
    children: 'slot',
    props: { label: text, value: bound, hint: text, tone: { kind: 'enum', options: ['gray', 'green', 'red', 'amber', 'blue'], default: 'gray' } },
  },
  Select: {
    source: 'ui',
    props: { label: text, placeholder: text, options: { kind: 'list', item: { kind: 'string', noun: 'option' } }, value: bound, onChange: event, disabled: bound },
  },
  DataTable: {
    source: 'DataTable',
    props: {
      columns: { kind: 'list', item: COLUMN },
      rows: bound,
      emptyText: text,
      searchPlaceholder: text,
      loading: bound,
      selectable: { kind: 'bool' },
      onRowClick: event,
      toolbar: slot,
      footer: slot,
    },
  },
  PageHeader: { source: 'resource', props: pageProps },
  Page: { source: 'resource', children: 'slot', props: pageProps },
  Panel: { source: 'resource', children: 'slot', props: { title: text, actions: slot } },
  ResourceTable: {
    source: 'resource',
    props: {
      resource: bound,
      columns: { kind: 'list', item: { kind: 'string', noun: 'column' } },
      filters: { kind: 'map' },
      pageSize: { kind: 'number', default: 20 },
      createLabel: text,
      emptyText: text,
      onSelect: event,
      actions: slot,
    },
  },
  ResourceForm: { source: 'resource', props: { resource: bound, fields: fieldNames, submitLabel: text, onSaved: event, onCancel: event } },
  StatTiles: { source: 'resource', props: { resource: bound, tiles: { kind: 'list', item: TILE } } },
  ExportButton: { source: 'resource', props: { resource: bound, filename: text, label: text } },
  Inbox: { source: 'resource', props: { resource: bound, states: { kind: 'list', item: { kind: 'string', noun: 'state' } }, fields: fieldNames, emptyText: text, onOpen: event } },
  AppShell: { source: 'resource', props: { title: text, screens: { kind: 'list', item: SCREEN } } },
  ListDetailLayout: {
    source: 'resource',
    props: {
      title: text,
      subtitle: text,
      resource: bound,
      columns: { kind: 'list', item: { kind: 'string', noun: 'column' } },
      filters: { kind: 'map' },
      emptyText: text,
      pageSize: { kind: 'number' },
      createLabel: text,
      exportable: { kind: 'bool', default: true },
    },
  },
  DashboardLayout: {
    source: 'resource',
    props: {
      title: text,
      subtitle: text,
      resource: bound,
      tiles: { kind: 'list', item: TILE },
      groupBy: text,
      sum: text,
      recentColumns: { kind: 'list', item: { kind: 'string', noun: 'column' } },
    },
  },
  FormPage: { source: 'resource', props: { title: text, subtitle: text, resource: bound, fields: fieldNames, submitLabel: text, onDone: event } },
  InboxLayout: {
    source: 'resource',
    props: { title: text, subtitle: text, resource: bound, states: { kind: 'list', item: { kind: 'string', noun: 'state' } }, fields: fieldNames, emptyText: text },
  },
  SettingsPage: { source: 'resource', props: { title: text, subtitle: text, resource: bound, fields: fieldNames } },
  RowActions: { source: 'review', props: { actions: bound } },
}

