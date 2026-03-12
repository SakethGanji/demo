import * as React from 'react'
import * as AvatarPrimitive from '@radix-ui/react-avatar'
import ContentEditable from 'react-contenteditable'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useCallback, useRef } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import type { RendererProps } from '../types'
import { defineComponent, registerComponent } from '../types'
import { useAppEditorStore } from '../stores'
import { useAppDocumentStore } from '../stores'
import { ToolbarSection, ToolbarItem } from '../inspector'
import { IconRenderer } from '../icons'
import { cn } from '@/shared/lib/utils'

/* ─── ui/avatar (inlined) ─── */

const Avatar = React.forwardRef<
  React.ComponentRef<typeof AvatarPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof AvatarPrimitive.Root>
>(({ className, ...props }, ref) => (
  <AvatarPrimitive.Root
    ref={ref}
    className={cn("relative flex shrink-0 overflow-hidden rounded-full", className)}
    {...props}
  />
))
Avatar.displayName = "Avatar"

const AvatarImage = React.forwardRef<
  React.ComponentRef<typeof AvatarPrimitive.Image>,
  React.ComponentPropsWithoutRef<typeof AvatarPrimitive.Image>
>(({ className, ...props }, ref) => (
  <AvatarPrimitive.Image
    ref={ref}
    className={cn("aspect-square h-full w-full", className)}
    {...props}
  />
))
AvatarImage.displayName = "AvatarImage"

const AvatarFallback = React.forwardRef<
  React.ComponentRef<typeof AvatarPrimitive.Fallback>,
  React.ComponentPropsWithoutRef<typeof AvatarPrimitive.Fallback>
>(({ className, ...props }, ref) => (
  <AvatarPrimitive.Fallback
    ref={ref}
    className={cn(
      "flex h-full w-full items-center justify-center rounded-full bg-muted text-sm font-medium",
      className
    )}
    {...props}
  />
))
AvatarFallback.displayName = "AvatarFallback"

export { Avatar, AvatarImage, AvatarFallback }

/* ─── ui/badge (inlined) ─── */

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground shadow-sm",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive: "border-transparent bg-destructive text-white shadow-sm",
        outline: "text-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

interface BadgeUIProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeUIProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
export type { BadgeUIProps as BadgeProps }

/* ═══════════════════════════════════════════════════════════════
   Text
   ═══════════════════════════════════════════════════════════════ */

interface TextProps {
  content: string
  format: 'plain' | 'markdown' | 'html'
  fontSize: string
  fontWeight: string
  color: string
  textAlign: string
  lineHeight: string
  letterSpacing: string
  textTransform: string
  opacity: string
  maxWidth: string
}

const TextComponent = ({ id, props, onEvent }: RendererProps<TextProps>) => {
  const mode = useAppEditorStore((s) => s.mode)
  const contentRef = useRef(props.content)
  contentRef.current = props.content

  const handleChange = useCallback(
    (e: { target: { value: string } }) => {
      useAppDocumentStore.getState().updateNodeProps(id, { content: e.target.value })
    },
    [id]
  )

  const style: React.CSSProperties = {
    fontSize: `${props.fontSize}px`,
    fontWeight: props.fontWeight,
    color: props.color || undefined,
    textAlign: props.textAlign as React.CSSProperties['textAlign'],
    lineHeight: props.lineHeight || undefined,
    letterSpacing: props.letterSpacing ? `${props.letterSpacing}px` : undefined,
    textTransform: props.textTransform as React.CSSProperties['textTransform'] || undefined,
    opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
    maxWidth: props.maxWidth || undefined,
  }

  const format = props.format || 'plain'

  if (format === 'markdown') {
    return (
      <div
        style={{
          ...style,
          '--tw-prose-body': 'currentColor',
          '--tw-prose-headings': 'currentColor',
          '--tw-prose-bold': 'currentColor',
          '--tw-prose-links': 'currentColor',
          '--tw-prose-code': 'currentColor',
          '--tw-prose-quotes': 'currentColor',
          '--tw-prose-bullets': 'currentColor',
          '--tw-prose-counters': 'currentColor',
        } as React.CSSProperties}
        className="w-full prose prose-sm max-w-none"
        onClick={() => onEvent?.('onClick')}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{props.content}</ReactMarkdown>
      </div>
    )
  }

  if (format === 'html') {
    return (
      <div
        style={{
          ...style,
          '--tw-prose-body': 'currentColor',
          '--tw-prose-headings': 'currentColor',
          '--tw-prose-bold': 'currentColor',
          '--tw-prose-links': 'currentColor',
          '--tw-prose-code': 'currentColor',
          '--tw-prose-quotes': 'currentColor',
          '--tw-prose-bullets': 'currentColor',
          '--tw-prose-counters': 'currentColor',
        } as React.CSSProperties}
        className="w-full prose prose-sm max-w-none"
        onClick={() => onEvent?.('onClick')}
        dangerouslySetInnerHTML={{ __html: props.content }}
      />
    )
  }

  if (mode !== 'edit') {
    return (
      <p
        style={style}
        className="w-full"
        onClick={() => onEvent?.('onClick')}
      >
        {props.content}
      </p>
    )
  }

  return (
    <ContentEditable
      html={props.content}
      disabled={false}
      onChange={handleChange}
      tagName="p"
      style={{ ...style, outline: 'none' }}
      className="w-full"
    />
  )
}

function TextSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem
          nodeId={nodeId}
          propKey="format"
          label="Format"
          type="radio"
          options={[
            { label: 'Plain', value: 'plain' },
            { label: 'Markdown', value: 'markdown' },
            { label: 'HTML', value: 'html' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Typography">
        <ToolbarItem nodeId={nodeId} propKey="fontSize" label="Font Size" type="slider" min={8} max={72} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="fontWeight"
          label="Weight"
          type="radio"
          options={[
            { label: 'Normal', value: '400' },
            { label: 'Medium', value: '500' },
            { label: 'Semi', value: '600' },
            { label: 'Bold', value: '700' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="textAlign"
          label="Align"
          type="radio"
          options={[
            { label: 'Left', value: 'left' },
            { label: 'Center', value: 'center' },
            { label: 'Right', value: 'right' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="textTransform"
          label="Transform"
          type="radio"
          options={[
            { label: 'None', value: '' },
            { label: 'Upper', value: 'uppercase' },
            { label: 'Lower', value: 'lowercase' },
            { label: 'Title', value: 'capitalize' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="lineHeight" label="Line Height" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="letterSpacing" label="Letter Spacing" type="slider" min={-2} max={10} />
      </ToolbarSection>

      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="color" label="Text Color" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
        <ToolbarItem nodeId={nodeId} propKey="maxWidth" label="Max Width" type="text" />
      </ToolbarSection>
    </>
  )
}

const textDefinition = defineComponent<TextProps>({
  type: 'Text',
  meta: {
    displayName: 'Text',
    icon: 'Type',
    category: 'content',
    defaultProps: {
      content: 'Hello, world!',
      format: 'plain',
      fontSize: '14',
      fontWeight: '400',
      color: '',
      textAlign: 'left',
      lineHeight: '',
      letterSpacing: '',
      textTransform: '',
      opacity: '100',
      maxWidth: '',
    },
  },
  propSchema: [
    { name: 'content', label: 'Content', section: 'Content', control: 'text', defaultValue: 'Hello, world!' },
    { name: 'format', label: 'Format', section: 'Content', control: 'select', defaultValue: 'plain', options: [{ label: 'Plain', value: 'plain' }, { label: 'Markdown', value: 'markdown' }, { label: 'HTML', value: 'html' }] },
    { name: 'fontSize', label: 'Font Size', section: 'Style', control: 'number', defaultValue: '14' },
    { name: 'fontWeight', label: 'Font Weight', section: 'Style', control: 'select', defaultValue: '400', options: [{ label: 'Normal', value: '400' }, { label: 'Medium', value: '500' }, { label: 'Semibold', value: '600' }, { label: 'Bold', value: '700' }] },
    { name: 'color', label: 'Color', section: 'Style', control: 'color', defaultValue: '' },
    { name: 'textAlign', label: 'Text Align', section: 'Style', control: 'select', defaultValue: 'left', options: [{ label: 'Left', value: 'left' }, { label: 'Center', value: 'center' }, { label: 'Right', value: 'right' }] },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: TextComponent,
  SettingsPanel: TextSettings,
})

registerComponent(textDefinition)

/* ═══════════════════════════════════════════════════════════════
   Heading
   ═══════════════════════════════════════════════════════════════ */

interface HeadingProps {
  content: string
  level: string
  color: string
  textAlign: string
  letterSpacing: string
  textTransform: string
  opacity: string
}

const headingSizeMap: Record<string, string> = {
  '1': '36',
  '2': '28',
  '3': '22',
  '4': '18',
}

const headingWeightMap: Record<string, string> = {
  '1': '800',
  '2': '700',
  '3': '600',
  '4': '600',
}

const HeadingComponent = ({ id, props, onEvent }: RendererProps<HeadingProps>) => {
  const mode = useAppEditorStore((s) => s.mode)

  const handleChange = useCallback(
    (e: { target: { value: string } }) => {
      useAppDocumentStore.getState().updateNodeProps(id, { content: e.target.value })
    },
    [id]
  )

  const style: React.CSSProperties = {
    fontSize: `${headingSizeMap[props.level] || '28'}px`,
    fontWeight: headingWeightMap[props.level] || '700',
    lineHeight: '1.2',
    color: props.color || undefined,
    textAlign: props.textAlign as React.CSSProperties['textAlign'],
    letterSpacing: props.letterSpacing ? `${props.letterSpacing}px` : undefined,
    textTransform: props.textTransform as React.CSSProperties['textTransform'] || undefined,
    opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
    margin: 0,
  }

  if (mode !== 'edit') {
    return (
      <div
        style={style}
        className="w-full"
        onClick={() => onEvent?.('onClick')}
      >
        {props.content}
      </div>
    )
  }

  return (
    <ContentEditable
      html={props.content}
      disabled={false}
      onChange={handleChange}
      tagName="div"
      style={{ ...style, outline: 'none' }}
      className="w-full"
    />
  )
}

function HeadingSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Heading">
        <ToolbarItem
          nodeId={nodeId}
          propKey="level"
          label="Level"
          type="radio"
          options={[
            { label: 'H1', value: '1' },
            { label: 'H2', value: '2' },
            { label: 'H3', value: '3' },
            { label: 'H4', value: '4' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="textAlign"
          label="Align"
          type="radio"
          options={[
            { label: 'Left', value: 'left' },
            { label: 'Center', value: 'center' },
            { label: 'Right', value: 'right' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="textTransform"
          label="Transform"
          type="radio"
          options={[
            { label: 'None', value: '' },
            { label: 'Upper', value: 'uppercase' },
            { label: 'Lower', value: 'lowercase' },
            { label: 'Title', value: 'capitalize' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="color" label="Text Color" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="letterSpacing" label="Letter Spacing" type="slider" min={-2} max={10} />
        <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
      </ToolbarSection>
    </>
  )
}

const headingDefinition = defineComponent<HeadingProps>({
  type: 'Heading',
  meta: {
    displayName: 'Heading',
    icon: 'Heading',
    category: 'content',
    defaultProps: {
      content: 'Heading',
      level: '2',
      color: '',
      textAlign: 'left',
      letterSpacing: '',
      textTransform: '',
      opacity: '100',
    },
  },
  propSchema: [
    { name: 'content', label: 'Content', section: 'Content', control: 'text', defaultValue: 'Heading' },
    { name: 'level', label: 'Level', section: 'Style', control: 'select', defaultValue: '2', options: [{ label: 'H1', value: '1' }, { label: 'H2', value: '2' }, { label: 'H3', value: '3' }, { label: 'H4', value: '4' }] },
    { name: 'color', label: 'Color', section: 'Style', control: 'color', defaultValue: '' },
    { name: 'textAlign', label: 'Text Align', section: 'Style', control: 'select', defaultValue: 'left', options: [{ label: 'Left', value: 'left' }, { label: 'Center', value: 'center' }, { label: 'Right', value: 'right' }] },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: HeadingComponent,
  SettingsPanel: HeadingSettings,
})

registerComponent(headingDefinition)

/* ═══════════════════════════════════════════════════════════════
   Badge
   ═══════════════════════════════════════════════════════════════ */

interface BadgeCompProps {
  text: string
  icon: string
  variant: 'default' | 'secondary' | 'destructive' | 'outline'
  cursor: string
}

const BadgeComponent = ({ props, onEvent }: RendererProps<BadgeCompProps>) => {
  return (
    <Badge
      variant={props.variant}
      style={{ cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined }}
      onClick={() => onEvent?.('onClick')}
      className="inline-flex items-center gap-1"
    >
      {props.icon && <IconRenderer name={props.icon} size={12} />}
      {props.text}
    </Badge>
  )
}

function BadgeSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="text" label="Text" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="icon" label="Icon" type="icon" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem
          nodeId={nodeId}
          propKey="variant"
          label="Variant"
          type="radio"
          options={[
            { label: 'Default', value: 'default' },
            { label: 'Secondary', value: 'secondary' },
            { label: 'Destructive', value: 'destructive' },
            { label: 'Outline', value: 'outline' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="cursor"
          label="Cursor"
          type="select"
          options={[
            { label: 'Default', value: 'default' },
            { label: 'Pointer', value: 'pointer' },
          ]}
        />
      </ToolbarSection>
    </>
  )
}

const badgeDefinition = defineComponent<BadgeCompProps>({
  type: 'Badge',
  meta: {
    displayName: 'Badge',
    icon: 'Tag',
    category: 'content',
    defaultProps: {
      text: 'Badge',
      icon: '',
      variant: 'default',
      cursor: 'default',
    },
  },
  propSchema: [
    { name: 'text', label: 'Text', section: 'Content', control: 'text', defaultValue: 'Badge' },
    { name: 'icon', label: 'Icon', section: 'Content', control: 'icon', defaultValue: '' },
    { name: 'variant', label: 'Variant', section: 'Style', control: 'select', defaultValue: 'default', options: [{ label: 'Default', value: 'default' }, { label: 'Secondary', value: 'secondary' }, { label: 'Destructive', value: 'destructive' }, { label: 'Outline', value: 'outline' }] },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: BadgeComponent,
  SettingsPanel: BadgeSettings,
})

registerComponent(badgeDefinition)

/* ═══════════════════════════════════════════════════════════════
   Image
   ═══════════════════════════════════════════════════════════════ */

interface ImageProps {
  src: string
  alt: string
  width: string
  height: string
  maxWidth: string
  borderRadius: string
  objectFit: string
  opacity: string
  cursor: string
}

const ImageComponent = ({ props, onEvent }: RendererProps<ImageProps>) => {
  if (!props.src) {
    return (
      <div
        className="w-full flex items-center justify-center bg-muted/50 text-muted-foreground text-xs"
        style={{
          aspectRatio: '16/9',
          width: props.width || undefined,
          maxWidth: props.maxWidth || undefined,
          borderRadius: props.borderRadius ? `${props.borderRadius}px` : 'var(--radius, 8px)',
        }}
      >
        No image source
      </div>
    )
  }

  return (
    <img
      src={props.src}
      alt={props.alt}
      onClick={() => onEvent?.('onClick')}
      className={!props.width ? 'w-full' : ''}
      style={{
        width: props.width || undefined,
        height: props.height || undefined,
        maxWidth: props.maxWidth || undefined,
        borderRadius: props.borderRadius ? `${props.borderRadius}px` : 'var(--radius, 8px)',
        objectFit: (props.objectFit || 'cover') as React.CSSProperties['objectFit'],
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined,
      }}
    />
  )
}

function ImageSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Image">
        <ToolbarItem nodeId={nodeId} propKey="src" label="Image URL" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="alt" label="Alt Text" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Dimensions">
        <div className="grid grid-cols-2 gap-x-2 gap-y-2">
          <ToolbarItem nodeId={nodeId} propKey="width" label="Width" type="text" />
          <ToolbarItem nodeId={nodeId} propKey="height" label="Height" type="text" />
        </div>
        <ToolbarItem nodeId={nodeId} propKey="maxWidth" label="Max Width" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="borderRadius" label="Radius" type="slider" max={50} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="objectFit"
          label="Fit"
          type="radio"
          options={[
            { label: 'Cover', value: 'cover' },
            { label: 'Contain', value: 'contain' },
            { label: 'Fill', value: 'fill' },
            { label: 'None', value: 'none' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="cursor"
          label="Cursor"
          type="select"
          options={[
            { label: 'Default', value: 'default' },
            { label: 'Pointer', value: 'pointer' },
            { label: 'Zoom In', value: 'zoom-in' },
          ]}
        />
      </ToolbarSection>
    </>
  )
}

const imageDefinition = defineComponent<ImageProps>({
  type: 'Image',
  meta: {
    displayName: 'Image',
    icon: 'ImageIcon',
    category: 'content',
    defaultProps: {
      src: 'https://images.unsplash.com/photo-1556761175-5973dc0f32e7?w=800&h=400&fit=crop',
      alt: 'Placeholder image',
      width: '',
      height: '',
      maxWidth: '',
      borderRadius: '',
      objectFit: 'cover',
      opacity: '100',
      cursor: 'default',
    },
  },
  propSchema: [
    { name: 'src', label: 'Source URL', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'alt', label: 'Alt Text', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'width', label: 'Width', section: 'Dimensions', control: 'text', defaultValue: '' },
    { name: 'height', label: 'Height', section: 'Dimensions', control: 'text', defaultValue: '' },
    { name: 'maxWidth', label: 'Max Width', section: 'Dimensions', control: 'text', defaultValue: '' },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: ImageComponent,
  SettingsPanel: ImageSettings,
})

registerComponent(imageDefinition)

/* ═══════════════════════════════════════════════════════════════
   Video
   ═══════════════════════════════════════════════════════════════ */

interface VideoProps {
  videoId: string
  autoplay: boolean
  muted: boolean
  controls: boolean
  borderRadius: string
  aspectRatio: string
}

const VideoComponent = ({ props }: RendererProps<VideoProps>) => {
  const mode = useAppEditorStore((s) => s.mode)

  const params = new URLSearchParams()
  if (props.autoplay) params.set('autoplay', '1')
  if (props.muted) params.set('mute', '1')
  if (!props.controls) params.set('controls', '0')
  const queryString = params.toString()

  return (
    <div
      className="w-full relative"
      style={{
        aspectRatio: props.aspectRatio || '16/9',
        borderRadius: props.borderRadius ? `${props.borderRadius}px` : undefined,
        overflow: props.borderRadius ? 'hidden' : undefined,
      }}
    >
      <iframe
        width="100%"
        height="100%"
        src={`https://www.youtube.com/embed/${props.videoId}${queryString ? '?' + queryString : ''}`}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
        style={{ pointerEvents: mode === 'edit' ? 'none' : undefined }}
      />
    </div>
  )
}

function VideoSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Video">
        <ToolbarItem nodeId={nodeId} propKey="videoId" label="YouTube Video ID" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Playback">
        <ToolbarItem nodeId={nodeId} propKey="autoplay" label="Autoplay" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="muted" label="Muted" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="controls" label="Show Controls" type="switch" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="borderRadius" label="Radius" type="slider" max={32} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="aspectRatio"
          label="Aspect Ratio"
          type="radio"
          options={[
            { label: '16:9', value: '16/9' },
            { label: '4:3', value: '4/3' },
            { label: '1:1', value: '1/1' },
            { label: '21:9', value: '21/9' },
          ]}
        />
      </ToolbarSection>
    </>
  )
}

const videoDefinition = defineComponent<VideoProps>({
  type: 'Video',
  meta: {
    displayName: 'Video',
    icon: 'Play',
    category: 'content',
    defaultProps: {
      videoId: 'IwzUs1IMdyQ',
      autoplay: false,
      muted: false,
      controls: true,
      borderRadius: '',
      aspectRatio: '16/9',
    },
  },
  propSchema: [
    { name: 'videoId', label: 'YouTube Video ID', section: 'Content', control: 'text', defaultValue: 'IwzUs1IMdyQ' },
    { name: 'autoplay', label: 'Autoplay', section: 'Playback', control: 'switch', defaultValue: false },
    { name: 'muted', label: 'Muted', section: 'Playback', control: 'switch', defaultValue: false },
    { name: 'controls', label: 'Show Controls', section: 'Playback', control: 'switch', defaultValue: true },
    { name: 'borderRadius', label: 'Radius', section: 'Style', control: 'number', defaultValue: '' },
    { name: 'aspectRatio', label: 'Aspect Ratio', section: 'Style', control: 'select', defaultValue: '16/9', options: [{ label: '16:9', value: '16/9' }, { label: '4:3', value: '4/3' }, { label: '1:1', value: '1/1' }] },
  ],
  eventSchema: [],
  exposedState: [],
  Component: VideoComponent,
  SettingsPanel: VideoSettings,
})

registerComponent(videoDefinition)

/* ═══════════════════════════════════════════════════════════════
   Avatar
   ═══════════════════════════════════════════════════════════════ */

interface AvatarCompProps {
  src: string
  fallback: string
  size: 'sm' | 'default' | 'lg' | 'xl'
  shape: 'circle' | 'square'
  borderWidth: string
  borderColor: string
  cursor: string
}

const avatarSizeClasses: Record<string, string> = {
  sm: 'h-8 w-8',
  default: 'h-10 w-10',
  lg: 'h-14 w-14',
  xl: 'h-20 w-20',
}

const AvatarComponent = ({ props, onEvent }: RendererProps<AvatarCompProps>) => {
  return (
    <Avatar
      className={avatarSizeClasses[props.size] || avatarSizeClasses.default}
      style={{
        borderRadius: props.shape === 'square' ? '8px' : undefined,
        borderWidth: props.borderWidth && props.borderWidth !== '0' ? `${props.borderWidth}px` : undefined,
        borderColor: props.borderColor || undefined,
        borderStyle: props.borderWidth && props.borderWidth !== '0' ? 'solid' : undefined,
        cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined,
      }}
      onClick={() => onEvent?.('onClick')}
    >
      {props.src && <AvatarImage src={props.src} alt={props.fallback} />}
      <AvatarFallback style={{ borderRadius: props.shape === 'square' ? '6px' : undefined }}>
        {props.fallback || 'AB'}
      </AvatarFallback>
    </Avatar>
  )
}

function AvatarSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="src" label="Image URL" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="fallback" label="Fallback" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem
          nodeId={nodeId}
          propKey="size"
          label="Size"
          type="radio"
          options={[
            { label: 'Sm', value: 'sm' },
            { label: 'Md', value: 'default' },
            { label: 'Lg', value: 'lg' },
            { label: 'XL', value: 'xl' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="shape"
          label="Shape"
          type="radio"
          options={[
            { label: 'Circle', value: 'circle' },
            { label: 'Square', value: 'square' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Border" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="borderWidth" label="Width" type="slider" max={6} />
        <ToolbarItem nodeId={nodeId} propKey="borderColor" label="Color" type="color" />
      </ToolbarSection>
    </>
  )
}

const avatarDefinition = defineComponent<AvatarCompProps>({
  type: 'Avatar',
  meta: {
    displayName: 'Avatar',
    icon: 'CircleUser',
    category: 'content',
    defaultProps: {
      src: '',
      fallback: 'AB',
      size: 'default',
      shape: 'circle',
      borderWidth: '0',
      borderColor: '',
      cursor: 'default',
    },
  },
  propSchema: [
    { name: 'src', label: 'Image URL', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'fallback', label: 'Fallback', section: 'Content', control: 'text', defaultValue: 'AB' },
    { name: 'size', label: 'Size', section: 'Style', control: 'select', defaultValue: 'default', options: [{ label: 'Small', value: 'sm' }, { label: 'Default', value: 'default' }, { label: 'Large', value: 'lg' }, { label: 'XL', value: 'xl' }] },
    { name: 'shape', label: 'Shape', section: 'Style', control: 'select', defaultValue: 'circle', options: [{ label: 'Circle', value: 'circle' }, { label: 'Square', value: 'square' }] },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: AvatarComponent,
  SettingsPanel: AvatarSettings,
})

registerComponent(avatarDefinition)

/* ═══════════════════════════════════════════════════════════════
   Icon
   ═══════════════════════════════════════════════════════════════ */

interface IconProps {
  icon: string
  size: string
  color: string
  strokeWidth: string
  opacity: string
  cursor: string
}

const IconComponent = ({ props, onEvent }: RendererProps<IconProps>) => {
  const size = Number(props.size) || 24

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: props.color || 'currentColor',
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        cursor: props.cursor && props.cursor !== 'default' ? props.cursor : undefined,
      }}
      onClick={() => onEvent?.('onClick')}
    >
      <IconRenderer
        name={props.icon}
        size={size}
        style={{ strokeWidth: Number(props.strokeWidth) || 2 }}
      />
    </div>
  )
}

function IconSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="icon" label="Icon" type="icon" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="size" label="Size" type="slider" max={96} />
        <ToolbarItem nodeId={nodeId} propKey="strokeWidth" label="Stroke" type="slider" max={4} />
        <ToolbarItem nodeId={nodeId} propKey="color" label="Color" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="cursor"
          label="Cursor"
          type="select"
          options={[
            { label: 'Default', value: 'default' },
            { label: 'Pointer', value: 'pointer' },
          ]}
        />
      </ToolbarSection>
    </>
  )
}

const iconDefinition = defineComponent<IconProps>({
  type: 'Icon',
  meta: {
    displayName: 'Icon',
    icon: 'Zap',
    category: 'content',
    defaultProps: {
      icon: 'star',
      size: '24',
      color: '',
      strokeWidth: '2',
      opacity: '100',
      cursor: 'default',
    },
  },
  propSchema: [
    { name: 'icon', label: 'Icon', section: 'Content', control: 'icon', defaultValue: 'star' },
    { name: 'size', label: 'Size', section: 'Style', control: 'number', defaultValue: '24', min: 8, max: 96 },
    { name: 'color', label: 'Color', section: 'Style', control: 'color', defaultValue: '' },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: IconComponent,
  SettingsPanel: IconSettings,
})

registerComponent(iconDefinition)

/* ═══════════════════════════════════════════════════════════════
   Link
   ═══════════════════════════════════════════════════════════════ */

interface LinkProps {
  text: string
  icon: string
  href: string
  target: '_self' | '_blank'
  fontSize: string
  fontWeight: string
  color: string
  textDecoration: 'none' | 'underline' | 'hover'
  opacity: string
}

const LinkComponent = ({ props, onEvent }: RendererProps<LinkProps>) => {
  const isEditMode = useAppEditorStore((s) => s.mode === 'edit')

  return (
    <a
      href={isEditMode ? undefined : (props.href || '#')}
      target={props.target}
      rel={props.target === '_blank' ? 'noopener noreferrer' : undefined}
      onClick={!isEditMode ? (e) => {
        onEvent?.('onClick')
        if (!props.href) e.preventDefault()
      } : (e) => e.preventDefault()}
      style={{
        fontSize: props.fontSize ? `${props.fontSize}px` : undefined,
        fontWeight: props.fontWeight || undefined,
        color: props.color || undefined,
        textDecoration: props.textDecoration === 'underline' ? 'underline' : 'none',
        opacity: props.opacity && props.opacity !== '100' ? Number(props.opacity) / 100 : undefined,
        cursor: isEditMode ? 'default' : 'pointer',
      }}
      className={`${props.textDecoration === 'hover' ? 'hover:underline' : ''} inline-flex items-center gap-1.5`}
    >
      {props.icon && <IconRenderer name={props.icon} size={props.fontSize ? Number(props.fontSize) : 14} />}
      {props.text}
    </a>
  )
}

function LinkSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="text" label="Text" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="icon" label="Icon" type="icon" />
        <ToolbarItem nodeId={nodeId} propKey="href" label="URL" type="text" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="target"
          label="Open In"
          type="radio"
          options={[
            { label: 'Same Tab', value: '_self' },
            { label: 'New Tab', value: '_blank' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="fontSize" label="Font Size" type="slider" max={48} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="fontWeight"
          label="Weight"
          type="radio"
          options={[
            { label: 'Normal', value: '400' },
            { label: 'Medium', value: '500' },
            { label: 'Bold', value: '700' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="color" label="Color" type="color" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="textDecoration"
          label="Underline"
          type="radio"
          options={[
            { label: 'None', value: 'none' },
            { label: 'Always', value: 'underline' },
            { label: 'On Hover', value: 'hover' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
      </ToolbarSection>
    </>
  )
}

const linkDefinition = defineComponent<LinkProps>({
  type: 'Link',
  meta: {
    displayName: 'Link',
    icon: 'ExternalLink',
    category: 'navigation',
    defaultProps: {
      text: 'Click here',
      icon: '',
      href: '',
      target: '_self',
      fontSize: '',
      fontWeight: '',
      color: '',
      textDecoration: 'underline',
      opacity: '100',
    },
  },
  propSchema: [
    { name: 'text', label: 'Text', section: 'Content', control: 'text', defaultValue: 'Click here' },
    { name: 'icon', label: 'Icon', section: 'Content', control: 'icon', defaultValue: '' },
    { name: 'href', label: 'URL', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'target', label: 'Open In', section: 'Content', control: 'select', defaultValue: '_self', options: [{ label: 'Same Tab', value: '_self' }, { label: 'New Tab', value: '_blank' }] },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [],
  Component: LinkComponent,
  SettingsPanel: LinkSettings,
})

registerComponent(linkDefinition)

/* ═══════════════════════════════════════════════════════════════
   List
   ═══════════════════════════════════════════════════════════════ */

/**
 * List — renders its children once per item in a data array.
 *
 * User sets the `data` prop to an expression like {{ stores.messages }}.
 * Children can reference {{ item.fieldName }} and {{ index }}.
 *
 * In edit mode: shows children once (the template).
 * In preview mode: resolves data, renders children N times.
 *
 * The actual repeat logic lives in NodeWrapper, which detects List nodes
 * and wraps each iteration in a RepeatContext.Provider.
 */

interface ListProps {
  data: string
  direction: 'column' | 'row'
  gap: string
  alignItems: string
  emptyText: string
}

const ListComponent = ({ props, children }: RendererProps<ListProps>) => {
  // The component itself just renders the layout wrapper.
  // The repeat logic is handled by NodeWrapper — by the time children
  // arrive here, they're already multiplied per item.
  const hasChildren = Array.isArray(children) ? children.length > 0 : !!children

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: props.direction || 'column',
        gap: `${props.gap || 0}px`,
        alignItems: props.alignItems || 'stretch',
        width: '100%',
      }}
    >
      {hasChildren ? (
        children
      ) : (
        <div className="flex items-center justify-center py-8 text-xs text-muted-foreground/60">
          {props.emptyText || 'No items'}
        </div>
      )}
    </div>
  )
}

function ListSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Data">
        <ToolbarItem
          nodeId={nodeId}
          propKey="data"
          label="Data Source"
          type="text"
          placeholder="{{ stores.myArray }}"
        />
        <p className="text-[10px] text-muted-foreground leading-relaxed mt-1">
          Bind to an array. Children can use <code className="bg-muted px-1 rounded">{'{{ item.field }}'}</code> and <code className="bg-muted px-1 rounded">{'{{ index }}'}</code>.
        </p>
      </ToolbarSection>
      <ToolbarSection title="Layout">
        <ToolbarItem
          nodeId={nodeId}
          propKey="direction"
          label="Direction"
          type="radio"
          options={[
            { label: 'Column', value: 'column' },
            { label: 'Row', value: 'row' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="gap" label="Gap" type="slider" max={48} />
        <ToolbarItem
          nodeId={nodeId}
          propKey="alignItems"
          label="Align"
          type="radio"
          options={[
            { label: 'Start', value: 'flex-start' },
            { label: 'Center', value: 'center' },
            { label: 'End', value: 'flex-end' },
            { label: 'Stretch', value: 'stretch' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Empty State" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="emptyText" label="Empty Text" type="text" placeholder="No items" />
      </ToolbarSection>
    </>
  )
}

const listDefinition = defineComponent<ListProps>({
  type: 'List',
  meta: {
    displayName: 'List',
    icon: 'List',
    category: 'layout',
    isContainer: true,
    defaultProps: {
      data: '',
      direction: 'column',
      gap: '8',
      alignItems: 'stretch',
      emptyText: 'No items',
    },
  },
  propSchema: [
    { name: 'data', label: 'Data Source', section: 'Data', control: 'expression', defaultValue: '' },
    { name: 'direction', label: 'Direction', section: 'Layout', control: 'select', defaultValue: 'column', options: [{ label: 'Column', value: 'column' }, { label: 'Row', value: 'row' }] },
    { name: 'gap', label: 'Gap', section: 'Layout', control: 'number', defaultValue: '8' },
  ],
  eventSchema: [],
  exposedState: [],
  Component: ListComponent,
  SettingsPanel: ListSettings,
})

registerComponent(listDefinition)
