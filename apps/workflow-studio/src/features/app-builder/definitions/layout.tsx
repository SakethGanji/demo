import * as React from 'react'
import { useMemo } from 'react'
import type { RendererProps } from '../types'
import { defineComponent, registerComponent, shadowMap } from '../types'
import { LayoutSettings } from '../inspector'
import { ToolbarSection, ToolbarItem } from '../inspector'
import { useComponentState } from '../hooks'
import { IconRenderer } from '../icons'
import { cn } from '@/shared/lib/utils'

/* ─── ui/card (inlined) ─── */

const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("rounded-lg border bg-card text-card-foreground shadow-sm", className)}
      {...props}
    />
  )
)
Card.displayName = "Card"

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col space-y-1.5 p-6", className)} {...props} />
  )
)
CardHeader.displayName = "CardHeader"

const CardTitle = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("font-semibold leading-none tracking-tight", className)} {...props} />
  )
)
CardTitle.displayName = "CardTitle"

const CardDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
  )
)
CardDescription.displayName = "CardDescription"

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
  )
)
CardContent.displayName = "CardContent"

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex items-center p-6 pt-0", className)} {...props} />
  )
)
CardFooter.displayName = "CardFooter"

export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent }

/* ─── ui/separator (inlined) ─── */

interface SeparatorProps extends React.HTMLAttributes<HTMLDivElement> {
  orientation?: "horizontal" | "vertical"
  decorative?: boolean
}

const Separator = React.forwardRef<HTMLDivElement, SeparatorProps>(
  ({ className, orientation = "horizontal", decorative = true, ...props }, ref) => (
    <div
      ref={ref}
      role={decorative ? "none" : "separator"}
      aria-orientation={decorative ? undefined : orientation}
      className={cn(
        "shrink-0 bg-border",
        orientation === "horizontal" ? "h-[1px] w-full" : "h-full w-[1px]",
        className
      )}
      {...props}
    />
  )
)
Separator.displayName = "Separator"

export { Separator }

/* ─── helpers ─── */

function parseCustomStyles(raw: string): React.CSSProperties {
  if (!raw) return {}
  try { return JSON.parse(raw) } catch { return {} }
}

/* ═══════════════════════════════════════════════════════════════
   Container
   ═══════════════════════════════════════════════════════════════ */

interface ContainerProps {
  // Dimensions
  width: string
  minWidth: string
  maxWidth: string
  height: string
  minHeight: string
  maxHeight: string
  // Flex
  flexDirection: 'column' | 'row'
  flexWrap: 'nowrap' | 'wrap'
  alignItems: string
  justifyContent: string
  fillSpace: string
  flexGrow: string
  flexShrink: string
  flexBasis: string
  alignSelf: string
  // Spacing
  paddingTop: string
  paddingRight: string
  paddingBottom: string
  paddingLeft: string
  marginTop: string
  marginRight: string
  marginBottom: string
  marginLeft: string
  gap: string
  // Box
  overflow: 'visible' | 'hidden' | 'scroll' | 'auto'
  position: 'static' | 'relative' | 'absolute' | 'sticky'
  top: string
  right: string
  bottom: string
  left: string
  zIndex: string
  // Colors
  background: string
  color: string
  opacity: string
  // Border
  borderRadius: string
  borderWidth: string
  borderColor: string
  borderStyle: 'none' | 'solid' | 'dashed' | 'dotted'
  // Effects
  shadow: string
  cursor: string
  // Escape hatch
  customStyles: string
}

const ContainerComponent = ({ props, children, onEvent }: RendererProps<ContainerProps>) => {
  const hasChildren = Array.isArray(children)
    ? children.length > 0
    : !!children

  // Build flex shorthand
  const flexGrow = props.flexGrow ? Number(props.flexGrow) : undefined
  const flexShrink = props.flexShrink ? Number(props.flexShrink) : undefined
  const flexBasis = props.flexBasis || undefined

  let flexValue: string | number | undefined
  if (props.fillSpace === 'yes') {
    flexValue = 1
  } else if (flexGrow !== undefined || flexShrink !== undefined || flexBasis) {
    flexValue = `${flexGrow ?? 0} ${flexShrink ?? 1} ${flexBasis ?? 'auto'}`
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: props.flexDirection,
        flexWrap: props.flexWrap !== 'nowrap' ? props.flexWrap : undefined,
        alignItems: props.alignItems || undefined,
        justifyContent: props.justifyContent || undefined,
        flex: flexValue,
        alignSelf: props.alignSelf && props.alignSelf !== 'auto' ? props.alignSelf : undefined,
        // Dimensions
        width: props.width || undefined,
        minWidth: props.minWidth || undefined,
        maxWidth: props.maxWidth || undefined,
        height: props.height || undefined,
        minHeight: props.minHeight || undefined,
        maxHeight: props.maxHeight || undefined,
        overflow: props.overflow !== 'visible' ? props.overflow : undefined,
        // Position
        position: props.position !== 'static' ? props.position as React.CSSProperties['position'] : undefined,
        top: props.top || undefined,
        right: props.right || undefined,
        bottom: props.bottom || undefined,
        left: props.left || undefined,
        zIndex: props.zIndex ? Number(props.zIndex) : undefined,
        // Spacing
        padding: `${props.paddingTop || 0}px ${props.paddingRight || 0}px ${props.paddingBottom || 0}px ${props.paddingLeft || 0}px`,
        margin: `${props.marginTop || 0}px ${props.marginRight || 0}px ${props.marginBottom || 0}px ${props.marginLeft || 0}px`,
        gap: `${props.gap}px`,
        // Colors
        backgroundColor: props.background === 'transparent' ? undefined : props.background || undefined,
        color: props.color || undefined,
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        // Border
        borderRadius: props.borderRadius && props.borderRadius !== '0' ? `${props.borderRadius}px` : undefined,
        borderWidth: props.borderWidth && props.borderWidth !== '0' ? `${props.borderWidth}px` : undefined,
        borderColor: props.borderColor || undefined,
        borderStyle: props.borderStyle !== 'none' ? props.borderStyle : (props.borderWidth && props.borderWidth !== '0' ? 'solid' : undefined),
        // Effects
        boxShadow: shadowMap[props.shadow] || undefined,
        cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined,
        // Custom CSS escape hatch
        ...parseCustomStyles(props.customStyles),
      }}
      className={cn('w-full')}
      onClick={() => onEvent?.('onClick')}
    >
      {hasChildren ? (
        children
      ) : (
        <div className="flex items-center justify-center py-10 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md">
          Drop components here
        </div>
      )}
    </div>
  )
}

function ContainerSettings({ nodeId }: { nodeId: string }) {
  return <LayoutSettings nodeId={nodeId} />
}

const containerDefinition = defineComponent<ContainerProps>({
  type: 'Container',
  meta: {
    displayName: 'Container',
    icon: 'LayoutTemplate',
    category: 'layout',
    isContainer: true,
    defaultProps: {
      width: '',
      minWidth: '',
      maxWidth: '',
      height: '',
      minHeight: '',
      maxHeight: '',
      flexDirection: 'column',
      flexWrap: 'nowrap',
      alignItems: 'stretch',
      justifyContent: 'flex-start',
      fillSpace: 'no',
      flexGrow: '',
      flexShrink: '',
      flexBasis: '',
      alignSelf: 'auto',
      paddingTop: '16',
      paddingRight: '16',
      paddingBottom: '16',
      paddingLeft: '16',
      marginTop: '0',
      marginRight: '0',
      marginBottom: '0',
      marginLeft: '0',
      gap: '12',
      overflow: 'visible',
      position: 'static',
      top: '',
      right: '',
      bottom: '',
      left: '',
      background: 'transparent',
      color: '',
      opacity: '100',
      borderRadius: '0',
      borderWidth: '0',
      borderColor: '',
      borderStyle: 'none',
      zIndex: '',
      shadow: '0',
      cursor: 'default',
      customStyles: '',
    },
  },
  propSchema: [
    { name: 'flexDirection', label: 'Direction', section: 'Layout', control: 'select', defaultValue: 'column', options: [{ label: 'Vertical', value: 'column' }, { label: 'Horizontal', value: 'row' }] },
    { name: 'gap', label: 'Gap', section: 'Layout', control: 'number', defaultValue: '12' },
    { name: 'background', label: 'Background', section: 'Style', control: 'color', defaultValue: 'transparent' },
    { name: 'minHeight', label: 'Min Height', section: 'Style', control: 'text', defaultValue: '' },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: ContainerComponent,
  SettingsPanel: ContainerSettings,
})

registerComponent(containerDefinition)

export default containerDefinition

/* ═══════════════════════════════════════════════════════════════
   Card
   ═══════════════════════════════════════════════════════════════ */

interface CardProps {
  width: string
  minWidth: string
  maxWidth: string
  height: string
  minHeight: string
  maxHeight: string
  flexDirection: 'column' | 'row'
  flexWrap: 'nowrap' | 'wrap'
  alignItems: string
  justifyContent: string
  fillSpace: string
  flexGrow: string
  flexShrink: string
  flexBasis: string
  alignSelf: string
  paddingTop: string
  paddingRight: string
  paddingBottom: string
  paddingLeft: string
  marginTop: string
  marginRight: string
  marginBottom: string
  marginLeft: string
  gap: string
  overflow: 'visible' | 'hidden' | 'scroll' | 'auto'
  position: 'static' | 'relative' | 'absolute' | 'sticky'
  top: string
  right: string
  bottom: string
  left: string
  zIndex: string
  background: string
  color: string
  opacity: string
  borderRadius: string
  borderWidth: string
  borderColor: string
  borderStyle: 'none' | 'solid' | 'dashed' | 'dotted'
  shadow: string
  cursor: string
  customStyles: string
}

const CardComponent = ({ props, children, onEvent }: RendererProps<CardProps>) => {
  const hasChildren = Array.isArray(children)
    ? children.length > 0
    : !!children

  const cardFlexGrow = props.flexGrow ? Number(props.flexGrow) : undefined
  const cardFlexShrink = props.flexShrink ? Number(props.flexShrink) : undefined
  const cardFlexBasis = props.flexBasis || undefined

  let cardFlexValue: string | number | undefined
  if (props.fillSpace === 'yes') {
    cardFlexValue = 1
  } else if (cardFlexGrow !== undefined || cardFlexShrink !== undefined || cardFlexBasis) {
    cardFlexValue = `${cardFlexGrow ?? 0} ${cardFlexShrink ?? 1} ${cardFlexBasis ?? 'auto'}`
  }

  return (
    <Card
      style={{
        display: 'flex',
        flexDirection: (props.flexDirection || 'column') as React.CSSProperties['flexDirection'],
        flexWrap: props.flexWrap === 'wrap' ? 'wrap' : undefined,
        alignItems: props.alignItems || undefined,
        justifyContent: props.justifyContent || undefined,
        flex: cardFlexValue,
        alignSelf: props.alignSelf && props.alignSelf !== 'auto' ? props.alignSelf : undefined,
        gap: `${props.gap || 12}px`,
        width: props.width || undefined,
        minWidth: props.minWidth || undefined,
        maxWidth: props.maxWidth || undefined,
        height: props.height || undefined,
        minHeight: props.minHeight || undefined,
        maxHeight: props.maxHeight || undefined,
        overflow: props.overflow && props.overflow !== 'visible' ? props.overflow : undefined,
        position: props.position !== 'static' ? props.position as React.CSSProperties['position'] : undefined,
        top: props.top || undefined,
        right: props.right || undefined,
        bottom: props.bottom || undefined,
        left: props.left || undefined,
        zIndex: props.zIndex ? Number(props.zIndex) : undefined,
        padding: `${props.paddingTop || 20}px ${props.paddingRight || 20}px ${props.paddingBottom || 20}px ${props.paddingLeft || 20}px`,
        margin: `${props.marginTop || 0}px ${props.marginRight || 0}px ${props.marginBottom || 0}px ${props.marginLeft || 0}px`,
        backgroundColor: props.background || undefined,
        color: props.color || undefined,
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        borderRadius: props.borderRadius ? `${props.borderRadius}px` : undefined,
        borderWidth: props.borderWidth && props.borderWidth !== '0' ? `${props.borderWidth}px` : undefined,
        borderColor: props.borderColor || undefined,
        borderStyle: props.borderStyle !== 'none' ? props.borderStyle : (props.borderWidth && props.borderWidth !== '0' ? 'solid' : undefined),
        boxShadow: props.shadow !== undefined ? (shadowMap[props.shadow] || shadowMap['1']) : undefined,
        cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined,
        ...parseCustomStyles(props.customStyles),
      }}
      className="w-full"
      onClick={() => onEvent?.('onClick')}
    >
      {hasChildren ? (
        children
      ) : (
        <div className="flex items-center justify-center py-8 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md">
          Drop components here
        </div>
      )}
    </Card>
  )
}

function CardSettings({ nodeId }: { nodeId: string }) {
  return <LayoutSettings nodeId={nodeId} />
}

const cardDefinition = defineComponent<CardProps>({
  type: 'Card',
  meta: {
    displayName: 'Card',
    icon: 'Square',
    category: 'layout',
    isContainer: true,
    defaultProps: {
      width: '',
      minWidth: '',
      maxWidth: '',
      height: '',
      minHeight: '',
      maxHeight: '',
      background: '',
      color: '',
      opacity: '100',
      borderRadius: '',
      borderWidth: '0',
      borderColor: '',
      borderStyle: 'none',
      shadow: '1',
      paddingTop: '20',
      paddingRight: '20',
      paddingBottom: '20',
      paddingLeft: '20',
      marginTop: '0',
      marginRight: '0',
      marginBottom: '0',
      marginLeft: '0',
      gap: '12',
      flexDirection: 'column',
      flexWrap: 'nowrap',
      alignItems: 'stretch',
      justifyContent: 'flex-start',
      overflow: 'visible',
      fillSpace: 'no',
      flexGrow: '',
      flexShrink: '',
      flexBasis: '',
      alignSelf: 'auto',
      position: 'static',
      top: '',
      right: '',
      bottom: '',
      left: '',
      zIndex: '',
      cursor: 'default',
      customStyles: '',
    },
  },
  propSchema: [],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: CardComponent,
  SettingsPanel: CardSettings,
})

registerComponent(cardDefinition)

/* ═══════════════════════════════════════════════════════════════
   ScrollArea
   ═══════════════════════════════════════════════════════════════ */

/**
 * ScrollArea — a Container variant with overflow:auto and flex:1.
 * Pre-configured for scrollable content areas (chat messages, data lists, etc.)
 */

interface ScrollAreaProps {
  width: string
  minWidth: string
  maxWidth: string
  height: string
  minHeight: string
  maxHeight: string
  flexDirection: 'column' | 'row'
  flexWrap: 'nowrap' | 'wrap'
  alignItems: string
  justifyContent: string
  fillSpace: string
  flexGrow: string
  flexShrink: string
  flexBasis: string
  alignSelf: string
  paddingTop: string
  paddingRight: string
  paddingBottom: string
  paddingLeft: string
  marginTop: string
  marginRight: string
  marginBottom: string
  marginLeft: string
  gap: string
  overflow: string
  position: string
  top: string
  right: string
  bottom: string
  left: string
  zIndex: string
  background: string
  color: string
  opacity: string
  borderRadius: string
  borderWidth: string
  borderColor: string
  borderStyle: string
  shadow: string
  cursor: string
  customStyles: string
}

const ScrollAreaComponent = ({ props, children, onEvent }: RendererProps<ScrollAreaProps>) => {
  const hasChildren = Array.isArray(children) ? children.length > 0 : !!children

  const saFlexGrow = props.flexGrow ? Number(props.flexGrow) : undefined
  const saFlexShrink = props.flexShrink ? Number(props.flexShrink) : undefined
  const saFlexBasis = props.flexBasis || undefined

  let saFlexValue: string | number | undefined
  if (props.fillSpace === 'yes') {
    saFlexValue = 1
  } else if (saFlexGrow !== undefined || saFlexShrink !== undefined || saFlexBasis) {
    saFlexValue = `${saFlexGrow ?? 0} ${saFlexShrink ?? 1} ${saFlexBasis ?? 'auto'}`
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: props.flexDirection as React.CSSProperties['flexDirection'],
        flexWrap: props.flexWrap !== 'nowrap' ? props.flexWrap as React.CSSProperties['flexWrap'] : undefined,
        alignItems: props.alignItems || undefined,
        justifyContent: props.justifyContent || undefined,
        flex: saFlexValue,
        alignSelf: props.alignSelf && props.alignSelf !== 'auto' ? props.alignSelf : undefined,
        width: props.width || undefined,
        minWidth: props.minWidth || undefined,
        maxWidth: props.maxWidth || undefined,
        height: props.height || undefined,
        minHeight: props.minHeight || undefined,
        maxHeight: props.maxHeight || undefined,
        overflow: props.overflow as React.CSSProperties['overflow'],
        position: props.position !== 'static' ? props.position as React.CSSProperties['position'] : undefined,
        top: props.top || undefined,
        right: props.right || undefined,
        bottom: props.bottom || undefined,
        left: props.left || undefined,
        zIndex: props.zIndex ? Number(props.zIndex) : undefined,
        padding: `${props.paddingTop || 0}px ${props.paddingRight || 0}px ${props.paddingBottom || 0}px ${props.paddingLeft || 0}px`,
        margin: `${props.marginTop || 0}px ${props.marginRight || 0}px ${props.marginBottom || 0}px ${props.marginLeft || 0}px`,
        gap: `${props.gap}px`,
        backgroundColor: props.background === 'transparent' ? undefined : props.background || undefined,
        color: props.color || undefined,
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        borderRadius: props.borderRadius && props.borderRadius !== '0' ? `${props.borderRadius}px` : undefined,
        borderWidth: props.borderWidth && props.borderWidth !== '0' ? `${props.borderWidth}px` : undefined,
        borderColor: props.borderColor || undefined,
        borderStyle: props.borderStyle !== 'none' ? props.borderStyle : (props.borderWidth && props.borderWidth !== '0' ? 'solid' : undefined),
        boxShadow: shadowMap[props.shadow] || undefined,
        cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined,
        ...parseCustomStyles(props.customStyles),
      }}
      className="w-full"
      onClick={() => onEvent?.('onClick')}
    >
      {hasChildren ? (
        children
      ) : (
        <div className="flex items-center justify-center py-10 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md">
          Scrollable area — drop content here
        </div>
      )}
    </div>
  )
}

function ScrollAreaSettings({ nodeId }: { nodeId: string }) {
  return <LayoutSettings nodeId={nodeId} />
}

const scrollAreaDefinition = defineComponent<ScrollAreaProps>({
  type: 'ScrollArea',
  meta: {
    displayName: 'Scroll Area',
    icon: 'ScrollText',
    category: 'layout',
    isContainer: true,
    defaultProps: {
      width: '',
      minWidth: '',
      maxWidth: '',
      height: '',
      minHeight: '',
      maxHeight: '400px',
      flexDirection: 'column',
      flexWrap: 'nowrap',
      alignItems: 'stretch',
      justifyContent: 'flex-start',
      fillSpace: 'yes',
      flexGrow: '1',
      flexShrink: '1',
      flexBasis: '0%',
      alignSelf: 'auto',
      paddingTop: '8',
      paddingRight: '8',
      paddingBottom: '8',
      paddingLeft: '8',
      marginTop: '0',
      marginRight: '0',
      marginBottom: '0',
      marginLeft: '0',
      gap: '8',
      overflow: 'auto',
      position: 'static',
      top: '',
      right: '',
      bottom: '',
      left: '',
      background: 'transparent',
      color: '',
      opacity: '100',
      borderRadius: '0',
      borderWidth: '0',
      borderColor: '',
      borderStyle: 'none',
      zIndex: '',
      shadow: '0',
      cursor: 'default',
      customStyles: '',
    },
  },
  propSchema: [],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: ScrollAreaComponent,
  SettingsPanel: ScrollAreaSettings,
})

registerComponent(scrollAreaDefinition)

/* ═══════════════════════════════════════════════════════════════
   Sidebar
   ═══════════════════════════════════════════════════════════════ */

/**
 * Sidebar — config-driven vertical navigation panel with a content area beside it.
 *
 * Items are defined via a simple text format:
 *   "Home, Settings, ---,  Account, Logout"
 *   "---" = separator
 *
 * Children are rendered to the right (or left) of the sidebar as the main content area.
 */

interface SidebarProps {
  width: string
  collapsedWidth: string
  collapsible: boolean
  side: 'left' | 'right'
  items: string
  header: string
  headerSize: string
  footer: string
  background: string
  color: string
  borderRight: boolean
  paddingTop: string
  paddingRight: string
  paddingBottom: string
  paddingLeft: string
  gap: string
}

interface NavItem {
  type: 'item' | 'separator'
  label: string
}

function parseItems(raw: string): NavItem[] {
  if (!raw) return []
  return raw.split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
    if (s === '---') return { type: 'separator' as const, label: '' }
    return { type: 'item' as const, label: s }
  })
}

// Map common nav item names to icon registry names
const labelToIcon: Record<string, string> = {
  home: 'home', dashboard: 'grid', analytics: 'bar-chart',
  users: 'users', settings: 'settings', help: 'help',
  inbox: 'inbox', logout: 'log-out', account: 'user',
  profile: 'user', search: 'search', mail: 'mail',
  notifications: 'bell', calendar: 'calendar', files: 'file',
  messages: 'message', products: 'package', orders: 'cart',
  billing: 'credit-card', security: 'shield', team: 'users',
}

function getIconForLabel(label: string): string {
  const key = label.toLowerCase().replace(/\s+/g, '')
  return labelToIcon[key] || 'chevron-right'
}

const SidebarComponent = ({ id, props, children, onEvent }: RendererProps<SidebarProps>) => {
  const { value: collapsed, setValue: setCollapsed } = useComponentState<boolean>(id, 'collapsed', false)
  const { value: activeItem, setValue: setActiveItem } = useComponentState<string>(id, 'activeItem', '')

  const items = useMemo(() => parseItems(props.items), [props.items])
  const isCollapsed = props.collapsible && collapsed
  const currentWidth = isCollapsed ? (props.collapsedWidth || '52px') : (props.width || '220px')

  const borderSide = props.side === 'right' ? 'borderLeft' : 'borderRight'

  // Default active to first item if unset
  const effectiveActive = (activeItem && items.some((i) => i.label === activeItem))
    ? activeItem
    : items.find((i) => i.type === 'item')?.label ?? ''

  const hasChildren = Array.isArray(children) ? children.length > 0 : !!children

  const sidebarPanel = (
    <aside
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: currentWidth,
        minWidth: currentWidth,
        height: '100%',
        backgroundColor: props.background || 'var(--sidebar-background, var(--muted))',
        color: props.color || 'var(--sidebar-foreground, var(--foreground))',
        [borderSide]: props.borderRight !== false ? '1px solid var(--border)' : undefined,
        paddingTop: `${props.paddingTop || 12}px`,
        paddingRight: `${props.paddingRight || 8}px`,
        paddingBottom: `${props.paddingBottom || 12}px`,
        paddingLeft: `${props.paddingLeft || 8}px`,
        gap: `${props.gap || 2}px`,
        transition: 'width 0.2s ease, min-width 0.2s ease',
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {/* Header */}
      {(props.header || props.collapsible) && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '4px 8px 8px',
            marginBottom: '4px',
            minHeight: '32px',
            flexShrink: 0,
          }}
        >
          {!isCollapsed && props.header && (
            <span style={{
              fontWeight: 600,
              fontSize: `${props.headerSize || 14}px`,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {props.header}
            </span>
          )}
          {props.collapsible && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                setCollapsed(!collapsed)
                onEvent?.(collapsed ? 'onExpand' : 'onCollapse')
              }}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                padding: '4px',
                borderRadius: '6px',
                color: 'var(--muted-foreground)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                marginLeft: isCollapsed ? 'auto' : undefined,
                marginRight: isCollapsed ? 'auto' : undefined,
              }}
              className="hover:bg-background/60 transition-colors"
              title={isCollapsed ? 'Expand' : 'Collapse'}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {isCollapsed ? (
                  <polyline points="9 18 15 12 9 6" />
                ) : (
                  <polyline points="15 18 9 12 15 6" />
                )}
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Nav items */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: `${props.gap || 2}px`, flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        {items.map((item, i) => {
          if (item.type === 'separator') {
            return <div key={`sep-${i}`} style={{ height: '1px', backgroundColor: 'var(--border)', margin: '6px 8px' }} />
          }

          const isActive = effectiveActive === item.label

          return (
            <button
              key={item.label}
              onClick={(e) => {
                e.stopPropagation()
                setActiveItem(item.label)
                onEvent?.('onNavigate', { label: item.label })
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: isCollapsed ? '8px' : '8px 12px',
                justifyContent: isCollapsed ? 'center' : 'flex-start',
                fontSize: '13px',
                fontWeight: isActive ? 500 : 400,
                color: isActive ? 'var(--foreground)' : 'var(--muted-foreground)',
                backgroundColor: isActive ? 'var(--background)' : 'transparent',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                textAlign: 'left',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                transition: 'all 0.15s ease',
                width: '100%',
              }}
              className="hover:bg-background/60 hover:text-foreground"
              title={isCollapsed ? item.label : undefined}
            >
              <IconRenderer name={getIconForLabel(item.label)} size={isCollapsed ? 20 : 18} style={{ flexShrink: 0 }} />
              {!isCollapsed && <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.label}</span>}
            </button>
          )
        })}
      </div>

      {/* Footer */}
      {props.footer && !isCollapsed && (
        <div style={{
          padding: '8px 12px',
          fontSize: '11px',
          color: 'var(--muted-foreground)',
          borderTop: '1px solid var(--border)',
          marginTop: '4px',
          paddingTop: '12px',
          flexShrink: 0,
        }}>
          {props.footer}
        </div>
      )}
    </aside>
  )

  const contentArea = (
    <div style={{ flex: 1, minWidth: 0, overflow: 'auto', height: '100%' }}>
      {hasChildren ? (
        children
      ) : (
        <div className="flex items-center justify-center py-10 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md m-4">
          Drop components here
        </div>
      )}
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'row', width: '100%', height: '100%' }}>
      {props.side === 'right' ? (
        <>
          {contentArea}
          {sidebarPanel}
        </>
      ) : (
        <>
          {sidebarPanel}
          {contentArea}
        </>
      )}
    </div>
  )
}

function SidebarSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="header" label="Header" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="items" label="Nav Items" type="text" placeholder="Home, Dashboard, ---, Settings" />
        <p className="text-[10px] text-muted-foreground leading-relaxed">
          Comma-separated. Use --- for separator.
        </p>
        <ToolbarItem nodeId={nodeId} propKey="footer" label="Footer Text" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Layout">
        <ToolbarItem nodeId={nodeId} propKey="width" label="Width" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="collapsible" label="Collapsible" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="collapsedWidth" label="Collapsed Width" type="text" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="side"
          label="Side"
          type="radio"
          options={[
            { label: 'Left', value: 'left' },
            { label: 'Right', value: 'right' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="gap" label="Gap" type="slider" max={16} />
      </ToolbarSection>
      <ToolbarSection title="Spacing" defaultOpen={false}>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          <ToolbarItem nodeId={nodeId} propKey="paddingTop" label="Top" type="slider" max={40} />
          <ToolbarItem nodeId={nodeId} propKey="paddingBottom" label="Bottom" type="slider" max={40} />
          <ToolbarItem nodeId={nodeId} propKey="paddingLeft" label="Left" type="slider" max={40} />
          <ToolbarItem nodeId={nodeId} propKey="paddingRight" label="Right" type="slider" max={40} />
        </div>
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="background" label="Background" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="color" label="Text Color" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="borderRight" label="Border" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="headerSize" label="Header Size" type="slider" max={24} />
      </ToolbarSection>
    </>
  )
}

const sidebarDefinition = defineComponent<SidebarProps>({
  type: 'Sidebar',
  meta: {
    displayName: 'Sidebar',
    icon: 'PanelLeft',
    category: 'navigation',
    isContainer: true,
    wrapperStyle: { flex: 1, minHeight: 0 },
    defaultProps: {
      width: '220px',
      collapsedWidth: '52px',
      collapsible: true,
      side: 'left',
      items: 'Home, Dashboard, Analytics, Users, ---, Settings, Help, ---, Logout',
      header: 'My App',
      headerSize: '14',
      footer: '',
      background: '',
      color: '',
      borderRight: true,
      paddingTop: '12',
      paddingRight: '8',
      paddingBottom: '12',
      paddingLeft: '8',
      gap: '2',
    },
    defaultChildren: [
      { type: 'Container', props: { __label: 'Content', paddingTop: '16', paddingRight: '16', paddingBottom: '16', paddingLeft: '16', gap: '12', minHeight: '100px' } },
    ],
  },
  propSchema: [
    { name: 'header', label: 'Header', section: 'Content', control: 'text', defaultValue: 'My App' },
    { name: 'items', label: 'Items', section: 'Content', control: 'text', defaultValue: 'Home, Dashboard, Analytics, Users, ---, Settings, Help' },
    { name: 'footer', label: 'Footer', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'width', label: 'Width', section: 'Layout', control: 'text', defaultValue: '220px' },
    { name: 'collapsible', label: 'Collapsible', section: 'Layout', control: 'switch', defaultValue: true },
    { name: 'side', label: 'Side', section: 'Layout', control: 'select', defaultValue: 'left', options: [{ label: 'Left', value: 'left' }, { label: 'Right', value: 'right' }] },
  ],
  eventSchema: [
    { name: 'onNavigate', label: 'On Navigate' },
    { name: 'onCollapse', label: 'On Collapse' },
    { name: 'onExpand', label: 'On Expand' },
  ],
  exposedState: [
    { name: 'collapsed', label: 'Collapsed', defaultValue: false },
    { name: 'activeItem', label: 'Active Item', defaultValue: '' },
  ],
  Component: SidebarComponent,
  SettingsPanel: SidebarSettings,
})

registerComponent(sidebarDefinition)

/* ═══════════════════════════════════════════════════════════════
   Navbar
   ═══════════════════════════════════════════════════════════════ */

/**
 * Navbar — config-driven top navigation bar with a content area below.
 *
 * Nav items are defined via comma-separated strings.
 * Format: "Label" or "Label:href"
 *   e.g. "Home:/,About:/about,Blog:/blog"
 *
 * Children are rendered below the navbar as the page content area.
 */

interface NavbarProps {
  brand: string
  brandSize: string
  links: string
  actionLabel: string
  actionVariant: 'default' | 'secondary' | 'outline' | 'ghost'
  height: string
  background: string
  color: string
  borderBottom: boolean
  sticky: boolean
  paddingLeft: string
  paddingRight: string
  gap: string
  shadow: string
}

function parseLinks(raw: string): Array<{ label: string; href: string }> {
  if (!raw) return []
  return raw.split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
    const idx = s.indexOf(':')
    if (idx > 0) return { label: s.slice(0, idx).trim(), href: s.slice(idx + 1).trim() }
    return { label: s, href: '#' }
  })
}

const NavbarComponent = ({ id, props, children, onEvent }: RendererProps<NavbarProps>) => {
  const links = parseLinks(props.links)
  const { value: activeLink, setValue: setActiveLink } = useComponentState<string>(
    id, 'activeLink', links[0]?.label ?? ''
  )

  const hasChildren = Array.isArray(children) ? children.length > 0 : !!children

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      {/* Nav bar */}
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          height: props.height || '56px',
          paddingLeft: `${props.paddingLeft || 16}px`,
          paddingRight: `${props.paddingRight || 16}px`,
          gap: `${props.gap || 8}px`,
          backgroundColor: props.background || 'var(--background)',
          color: props.color || 'var(--foreground)',
          borderBottom: props.borderBottom ? '1px solid var(--border)' : undefined,
          position: props.sticky ? 'sticky' : undefined,
          top: props.sticky ? 0 : undefined,
          zIndex: props.sticky ? 40 : undefined,
          boxShadow: props.shadow === '1' ? '0 1px 3px rgba(0,0,0,0.08)' : props.shadow === '2' ? '0 2px 8px rgba(0,0,0,0.1)' : undefined,
          width: '100%',
          flexShrink: 0,
        }}
      >
        {/* Brand */}
        {props.brand && (
          <div
            style={{
              fontWeight: 600,
              fontSize: `${props.brandSize || 18}px`,
              whiteSpace: 'nowrap',
              marginRight: '12px',
              cursor: 'default',
            }}
          >
            {props.brand}
          </div>
        )}

        {/* Nav links */}
        <div style={{ display: 'flex', alignItems: 'center', gap: `${props.gap || 8}px`, flex: 1 }}>
          {links.map((link) => {
            const isActive = activeLink === link.label
            return (
              <a
                key={link.label}
                href={link.href}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setActiveLink(link.label)
                  onEvent?.('onNavigate', { label: link.label, href: link.href })
                }}
                style={{
                  padding: '6px 12px',
                  fontSize: '14px',
                  fontWeight: isActive ? 500 : 400,
                  color: isActive ? 'var(--foreground)' : 'var(--muted-foreground)',
                  textDecoration: 'none',
                  borderRadius: '6px',
                  transition: 'all 0.15s ease',
                  cursor: 'pointer',
                  backgroundColor: isActive ? 'var(--muted)' : undefined,
                }}
                className="hover:bg-muted/60 hover:text-foreground"
              >
                {link.label}
              </a>
            )
          })}
        </div>

        {/* Action button (optional) */}
        {props.actionLabel && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onEvent?.('onAction')
            }}
            style={{
              padding: '6px 16px',
              fontSize: '13px',
              fontWeight: 500,
              borderRadius: '6px',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              border: props.actionVariant === 'outline' ? '1px solid var(--border)' : 'none',
              backgroundColor: props.actionVariant === 'default' ? 'var(--primary)' :
                props.actionVariant === 'secondary' ? 'var(--secondary)' : 'transparent',
              color: props.actionVariant === 'default' ? 'var(--primary-foreground)' :
                props.actionVariant === 'ghost' ? 'var(--foreground)' : 'var(--foreground)',
            }}
            className="hover:opacity-90 transition-opacity"
          >
            {props.actionLabel}
          </button>
        )}
      </nav>

      {/* Content area — children go here */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {hasChildren ? (
          children
        ) : (
          <div className="flex items-center justify-center py-10 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md m-4">
            Drop components here
          </div>
        )}
      </div>
    </div>
  )
}

function NavbarSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Brand">
        <ToolbarItem nodeId={nodeId} propKey="brand" label="Brand Text" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="brandSize" label="Brand Size" type="slider" max={36} />
      </ToolbarSection>
      <ToolbarSection title="Links">
        <ToolbarItem nodeId={nodeId} propKey="links" label="Nav Links" type="text" placeholder="Home:/,About,Blog" />
        <p className="text-[10px] text-muted-foreground leading-relaxed">
          Comma-separated. Format: Label or Label:/path
        </p>
      </ToolbarSection>
      <ToolbarSection title="Action Button" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="actionLabel" label="Label" type="text" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="actionVariant"
          label="Variant"
          type="radio"
          options={[
            { label: 'Primary', value: 'default' },
            { label: 'Secondary', value: 'secondary' },
            { label: 'Outline', value: 'outline' },
            { label: 'Ghost', value: 'ghost' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Layout">
        <ToolbarItem nodeId={nodeId} propKey="height" label="Height" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="gap" label="Gap" type="slider" max={40} />
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          <ToolbarItem nodeId={nodeId} propKey="paddingLeft" label="Pad Left" type="slider" max={60} />
          <ToolbarItem nodeId={nodeId} propKey="paddingRight" label="Pad Right" type="slider" max={60} />
        </div>
        <ToolbarItem nodeId={nodeId} propKey="sticky" label="Sticky" type="switch" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="background" label="Background" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="color" label="Text Color" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="borderBottom" label="Border Bottom" type="switch" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="shadow"
          label="Shadow"
          type="radio"
          options={[
            { label: 'None', value: '0' },
            { label: 'Sm', value: '1' },
            { label: 'Md', value: '2' },
          ]}
        />
      </ToolbarSection>
    </>
  )
}

const navbarDefinition = defineComponent<NavbarProps>({
  type: 'Navbar',
  meta: {
    displayName: 'Navbar',
    icon: 'PanelTop',
    category: 'navigation',
    isContainer: true,
    wrapperStyle: { flex: 1, minHeight: 0 },
    defaultProps: {
      brand: 'My App',
      brandSize: '18',
      links: 'Home, About, Blog, Contact',
      actionLabel: 'Sign In',
      actionVariant: 'default',
      height: '56px',
      background: '',
      color: '',
      borderBottom: true,
      sticky: false,
      paddingLeft: '20',
      paddingRight: '20',
      gap: '4',
      shadow: '0',
    },
    defaultChildren: [
      { type: 'Container', props: { __label: 'Content', paddingTop: '16', paddingRight: '16', paddingBottom: '16', paddingLeft: '16', gap: '12', minHeight: '100px' } },
    ],
  },
  propSchema: [
    { name: 'brand', label: 'Brand', section: 'Brand', control: 'text', defaultValue: 'My App' },
    { name: 'links', label: 'Links', section: 'Links', control: 'text', defaultValue: 'Home, About, Blog, Contact' },
    { name: 'actionLabel', label: 'Action', section: 'Action', control: 'text', defaultValue: 'Sign In' },
    { name: 'height', label: 'Height', section: 'Layout', control: 'text', defaultValue: '56px' },
    { name: 'sticky', label: 'Sticky', section: 'Layout', control: 'switch', defaultValue: false },
    { name: 'borderBottom', label: 'Border', section: 'Style', control: 'switch', defaultValue: true },
  ],
  eventSchema: [
    { name: 'onNavigate', label: 'On Navigate' },
    { name: 'onAction', label: 'On Action Click' },
  ],
  exposedState: [
    { name: 'activeLink', label: 'Active Link', defaultValue: '' },
  ],
  Component: NavbarComponent,
  SettingsPanel: NavbarSettings,
})

registerComponent(navbarDefinition)

/* ═══════════════════════════════════════════════════════════════
   Spacer
   ═══════════════════════════════════════════════════════════════ */

/**
 * Spacer — a flexible empty element that pushes siblings apart.
 * Like a spring in a flex container. Drop between elements to
 * push them to opposite ends.
 */

interface SpacerProps {
  size: string
  direction: 'both' | 'horizontal' | 'vertical'
}

const SpacerComponent = ({ props }: RendererProps<SpacerProps>) => {
  const style: React.CSSProperties = {}

  if (props.size) {
    // Fixed size spacer
    if (props.direction === 'horizontal') {
      style.width = props.size
      style.minWidth = props.size
    } else if (props.direction === 'vertical') {
      style.height = props.size
      style.minHeight = props.size
    } else {
      style.flex = `0 0 ${props.size}`
    }
  } else {
    // Flexible spacer — fills remaining space
    style.flex = 1
  }

  return (
    <div style={style} className="spacer-component" />
  )
}

const spacerDefinition = defineComponent<SpacerProps>({
  type: 'Spacer',
  meta: {
    displayName: 'Spacer',
    icon: 'Space',
    category: 'layout',
    defaultProps: {
      size: '',
      direction: 'both',
    },
  },
  propSchema: [
    {
      name: 'size',
      label: 'Size',
      section: 'Layout',
      control: 'text',
      defaultValue: '',
    },
    {
      name: 'direction',
      label: 'Direction',
      section: 'Layout',
      control: 'select',
      defaultValue: 'both',
      options: [
        { label: 'Both', value: 'both' },
        { label: 'Horizontal', value: 'horizontal' },
        { label: 'Vertical', value: 'vertical' },
      ],
    },
  ],
  eventSchema: [],
  exposedState: [],
  Component: SpacerComponent,
})

registerComponent(spacerDefinition)

/* ═══════════════════════════════════════════════════════════════
   Divider
   ═══════════════════════════════════════════════════════════════ */

interface DividerProps {
  color: string
  thickness: string
  marginY: string
  maxWidth: string
  opacity: string
  style: 'solid' | 'dashed' | 'dotted'
}

const DividerComponent = ({ props }: RendererProps<DividerProps>) => {
  const useBorder = props.style && props.style !== 'solid'

  return (
    <Separator
      className="w-full"
      style={{
        height: useBorder ? 0 : `${props.thickness || 1}px`,
        borderTopWidth: useBorder ? `${props.thickness || 1}px` : undefined,
        borderTopStyle: useBorder ? props.style : undefined,
        borderTopColor: useBorder ? (props.color || undefined) : undefined,
        margin: `${props.marginY || 8}px auto`,
        maxWidth: props.maxWidth || undefined,
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        ...(props.color && !useBorder ? { backgroundColor: props.color } : {}),
        ...(!useBorder ? { border: 'none' } : { backgroundColor: 'transparent' }),
      }}
    />
  )
}

function DividerSettings({ nodeId }: { nodeId: string }) {
  return (
    <ToolbarSection title="Divider">
      <ToolbarItem
        nodeId={nodeId}
        propKey="style"
        label="Style"
        type="radio"
        options={[
          { label: 'Solid', value: 'solid' },
          { label: 'Dashed', value: 'dashed' },
          { label: 'Dotted', value: 'dotted' },
        ]}
      />
      <ToolbarItem nodeId={nodeId} propKey="thickness" label="Thickness" type="slider" min={1} max={8} />
      <ToolbarItem nodeId={nodeId} propKey="marginY" label="Vertical Spacing" type="slider" max={48} />
      <ToolbarItem nodeId={nodeId} propKey="maxWidth" label="Max Width" type="text" />
      <ToolbarItem nodeId={nodeId} propKey="color" label="Color" type="color" />
      <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
    </ToolbarSection>
  )
}

const dividerDefinition = defineComponent<DividerProps>({
  type: 'Divider',
  meta: {
    displayName: 'Divider',
    icon: 'Minus',
    category: 'layout',
    defaultProps: {
      color: '',
      thickness: '1',
      marginY: '8',
      maxWidth: '',
      opacity: '100',
      style: 'solid',
    },
  },
  propSchema: [
    { name: 'thickness', label: 'Thickness', section: 'Style', control: 'number', defaultValue: '1' },
    { name: 'color', label: 'Color', section: 'Style', control: 'color', defaultValue: '' },
    { name: 'style', label: 'Style', section: 'Style', control: 'select', defaultValue: 'solid', options: [{ label: 'Solid', value: 'solid' }, { label: 'Dashed', value: 'dashed' }, { label: 'Dotted', value: 'dotted' }] },
  ],
  eventSchema: [],
  exposedState: [],
  Component: DividerComponent,
  SettingsPanel: DividerSettings,
})

registerComponent(dividerDefinition)
