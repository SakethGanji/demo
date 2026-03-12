import * as React from 'react'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { Upload } from 'lucide-react'
import type { RendererProps } from '../types'
import { defineComponent, registerComponent } from '../types'
import { ToolbarSection, ToolbarItem } from '../inspector'
import { useComponentState } from '../hooks'
import { useRuntimeStateStore } from '../stores'
import { IconRenderer } from '../icons'
import { cn } from '@/shared/lib/utils'

/* ─── ui/button (inlined) ─── */

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap text-sm font-medium transition-colors cursor-pointer disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 [&_svg]:shrink-0 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-2",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90",
        secondary: "bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
        destructive: "bg-destructive text-white shadow-sm hover:bg-destructive/90",
        outline: "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        default: "h-9 px-4 py-2",
        lg: "h-10 px-6",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

interface ButtonUIProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonUIProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
export type { ButtonUIProps as ButtonProps }

/* ─── ui/input (inlined) ─── */

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      ref={ref}
      {...props}
    />
  )
)
Input.displayName = "Input"

export { Input }

/* ─── ui/label (inlined) ─── */

const Label = React.forwardRef<HTMLLabelElement, React.LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        "text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
        className
      )}
      {...props}
    />
  )
)
Label.displayName = "Label"

export { Label }

/* ─── ui/switch (inlined) ─── */

const Switch = React.forwardRef<
  React.ComponentRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    className={cn(
      "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      className
    )}
    {...props}
    ref={ref}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        "pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
      )}
    />
  </SwitchPrimitive.Root>
))
Switch.displayName = "Switch"

export { Switch }

/* ─── ui/textarea (inlined) ─── */

const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      className={cn(
        "flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      ref={ref}
      {...props}
    />
  )
)
Textarea.displayName = "Textarea"

export { Textarea }

/* ═══════════════════════════════════════════════════════════════
   Button
   ═══════════════════════════════════════════════════════════════ */

interface ButtonCompProps {
  label: string
  icon: string
  iconPosition: 'left' | 'right'
  variant: 'default' | 'secondary' | 'outline' | 'destructive' | 'ghost'
  size: 'sm' | 'default' | 'lg'
  fullWidth: boolean
  disabled: boolean
  loading: boolean
}

const ButtonComponent = ({ id, props, onEvent }: RendererProps<ButtonCompProps>) => {
  // Read from component state so setComponentState can control loading/disabled at runtime
  const { value: stateDisabled } = useComponentState<boolean>(id, 'disabled', false)
  const { value: stateLoading } = useComponentState<boolean>(id, 'loading', false)

  const disabled = props.disabled || stateDisabled
  const loading = props.loading || stateLoading

  return (
    <Button
      variant={props.variant}
      size={props.size}
      className={props.fullWidth ? 'w-full' : ''}
      disabled={disabled || loading}
      onClick={() => onEvent?.('onClick')}
    >
      {loading && (
        <svg className="animate-spin -ml-1 mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {!loading && props.icon && props.iconPosition !== 'right' && (
        <IconRenderer name={props.icon} size={props.size === 'sm' ? 14 : props.size === 'lg' ? 18 : 16} />
      )}
      {props.label && <span>{props.label}</span>}
      {!loading && props.icon && props.iconPosition === 'right' && (
        <IconRenderer name={props.icon} size={props.size === 'sm' ? 14 : props.size === 'lg' ? 18 : 16} />
      )}
    </Button>
  )
}

function ButtonSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="label" label="Label" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="icon" label="Icon" type="icon" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="iconPosition"
          label="Icon Position"
          type="radio"
          options={[
            { label: 'Left', value: 'left' },
            { label: 'Right', value: 'right' },
          ]}
        />
      </ToolbarSection>

      <ToolbarSection title="Style">
        <ToolbarItem
          nodeId={nodeId}
          propKey="variant"
          label="Variant"
          type="radio"
          options={[
            { label: 'Primary', value: 'default' },
            { label: 'Secondary', value: 'secondary' },
            { label: 'Outline', value: 'outline' },
            { label: 'Destructive', value: 'destructive' },
            { label: 'Ghost', value: 'ghost' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="size"
          label="Size"
          type="radio"
          options={[
            { label: 'Sm', value: 'sm' },
            { label: 'Default', value: 'default' },
            { label: 'Lg', value: 'lg' },
          ]}
        />
      </ToolbarSection>

      <ToolbarSection title="Layout">
        <ToolbarItem nodeId={nodeId} propKey="fullWidth" label="Full Width" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="loading" label="Loading" type="switch" />
      </ToolbarSection>
    </>
  )
}

const buttonDefinition = defineComponent<ButtonCompProps>({
  type: 'Button',
  meta: {
    displayName: 'Button',
    icon: 'MousePointerClick',
    category: 'input',
    defaultProps: {
      label: 'Click me',
      icon: '',
      iconPosition: 'left',
      variant: 'default',
      size: 'default',
      fullWidth: false,
      disabled: false,
      loading: false,
    },
  },
  propSchema: [
    { name: 'label', label: 'Label', section: 'Content', control: 'text', defaultValue: 'Click me' },
    { name: 'icon', label: 'Icon', section: 'Content', control: 'icon', defaultValue: '' },
    { name: 'iconPosition', label: 'Icon Position', section: 'Content', control: 'select', defaultValue: 'left', options: [{ label: 'Left', value: 'left' }, { label: 'Right', value: 'right' }] },
    { name: 'variant', label: 'Variant', section: 'Style', control: 'select', defaultValue: 'default', options: [{ label: 'Primary', value: 'default' }, { label: 'Secondary', value: 'secondary' }, { label: 'Outline', value: 'outline' }, { label: 'Destructive', value: 'destructive' }, { label: 'Ghost', value: 'ghost' }] },
    { name: 'size', label: 'Size', section: 'Style', control: 'select', defaultValue: 'default', options: [{ label: 'Small', value: 'sm' }, { label: 'Default', value: 'default' }, { label: 'Large', value: 'lg' }] },
    { name: 'fullWidth', label: 'Full Width', section: 'Layout', control: 'switch', defaultValue: false },
    { name: 'disabled', label: 'Disabled', section: 'Layout', control: 'switch', defaultValue: false },
    { name: 'loading', label: 'Loading', section: 'Layout', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onClick', label: 'On Click' },
  ],
  exposedState: [
    { name: 'disabled', label: 'Disabled', defaultValue: false },
    { name: 'loading', label: 'Loading', defaultValue: false },
  ],
  Component: ButtonComponent,
  SettingsPanel: ButtonSettings,
})

registerComponent(buttonDefinition)

/* ═══════════════════════════════════════════════════════════════
   Input
   ═══════════════════════════════════════════════════════════════ */

interface InputCompProps {
  placeholder: string
  type: 'text' | 'email' | 'password' | 'number' | 'url' | 'tel' | 'search'
  disabled: boolean
  fullWidth: boolean
  required: boolean
  maxLength: string
  pattern: string
  defaultValue: string
}

const InputComponent = ({ id, props, onEvent }: RendererProps<InputCompProps>) => {
  const { value, setValue } = useComponentState<string>(id, 'value', props.defaultValue || '')

  return (
    <Input
      placeholder={props.placeholder}
      type={props.type}
      disabled={props.disabled}
      required={props.required}
      maxLength={props.maxLength ? Number(props.maxLength) : undefined}
      pattern={props.pattern || undefined}
      value={value}
      onChange={(e) => {
        setValue(e.target.value)
        onEvent?.('onChange', e.target.value)
      }}
      onFocus={() => onEvent?.('onFocus')}
      onBlur={() => onEvent?.('onBlur')}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onEvent?.('onSubmit', value)
      }}
      onClick={(e) => e.stopPropagation()}
      className={cn(props.fullWidth ? 'w-full' : '')}
    />
  )
}

function InputSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="placeholder" label="Placeholder" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="defaultValue" label="Default Value" type="text" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="type"
          label="Type"
          type="radio"
          options={[
            { label: 'Text', value: 'text' },
            { label: 'Email', value: 'email' },
            { label: 'Password', value: 'password' },
            { label: 'Number', value: 'number' },
            { label: 'URL', value: 'url' },
            { label: 'Tel', value: 'tel' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Validation" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="required" label="Required" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="maxLength" label="Max Length" type="number" />
        <ToolbarItem nodeId={nodeId} propKey="pattern" label="Pattern (Regex)" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Layout">
        <ToolbarItem nodeId={nodeId} propKey="fullWidth" label="Full Width" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
      </ToolbarSection>
    </>
  )
}

const inputDefinition = defineComponent<InputCompProps>({
  type: 'Input',
  meta: {
    displayName: 'Input',
    icon: 'TextCursorInput',
    category: 'input',
    defaultProps: {
      placeholder: 'Enter text...',
      type: 'text',
      disabled: false,
      fullWidth: true,
      required: false,
      maxLength: '',
      pattern: '',
      defaultValue: '',
    },
  },
  propSchema: [
    { name: 'placeholder', label: 'Placeholder', section: 'Content', control: 'text', defaultValue: 'Enter text...' },
    { name: 'defaultValue', label: 'Default Value', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'type', label: 'Type', section: 'Content', control: 'select', defaultValue: 'text', options: [{ label: 'Text', value: 'text' }, { label: 'Email', value: 'email' }, { label: 'Password', value: 'password' }, { label: 'Number', value: 'number' }, { label: 'URL', value: 'url' }, { label: 'Tel', value: 'tel' }] },
    { name: 'required', label: 'Required', section: 'Validation', control: 'switch', defaultValue: false },
    { name: 'maxLength', label: 'Max Length', section: 'Validation', control: 'number', defaultValue: '' },
    { name: 'fullWidth', label: 'Full Width', section: 'Layout', control: 'switch', defaultValue: true },
    { name: 'disabled', label: 'Disabled', section: 'Layout', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On Change' },
    { name: 'onFocus', label: 'On Focus' },
    { name: 'onBlur', label: 'On Blur' },
    { name: 'onSubmit', label: 'On Enter' },
  ],
  exposedState: [
    { name: 'value', label: 'Value', defaultValue: '' },
  ],
  Component: InputComponent,
  SettingsPanel: InputSettings,
})

registerComponent(inputDefinition)

/* ═══════════════════════════════════════════════════════════════
   Textarea
   ═══════════════════════════════════════════════════════════════ */

interface TextareaCompProps {
  placeholder: string
  rows: number
  disabled: boolean
  fullWidth: boolean
  resize: 'none' | 'vertical' | 'both'
  maxLength: string
}

const TextareaComponent = ({ id, props, onEvent }: RendererProps<TextareaCompProps>) => {
  const { value, setValue } = useComponentState<string>(id, 'value', '')

  return (
    <Textarea
      placeholder={props.placeholder}
      rows={props.rows}
      disabled={props.disabled}
      value={value}
      onChange={(e) => {
        setValue(e.target.value)
        onEvent?.('onChange', e.target.value)
      }}
      onFocus={() => onEvent?.('onFocus')}
      onBlur={() => onEvent?.('onBlur', value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault()
          onEvent?.('onSubmit', value)
        }
      }}
      onClick={(e) => e.stopPropagation()}
      maxLength={props.maxLength ? Number(props.maxLength) : undefined}
      style={{ resize: props.resize || 'vertical' }}
      className={cn(props.fullWidth !== false ? 'w-full' : '')}
    />
  )
}

function TextareaSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="placeholder" label="Placeholder" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="rows" label="Rows" type="slider" min={2} max={12} />
        <ToolbarItem nodeId={nodeId} propKey="maxLength" label="Max Length" type="number" />
      </ToolbarSection>
      <ToolbarSection title="Layout">
        <ToolbarItem nodeId={nodeId} propKey="fullWidth" label="Full Width" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="resize"
          label="Resize"
          type="radio"
          options={[
            { label: 'None', value: 'none' },
            { label: 'Vertical', value: 'vertical' },
            { label: 'Both', value: 'both' },
          ]}
        />
      </ToolbarSection>
    </>
  )
}

const textareaDefinition = defineComponent<TextareaCompProps>({
  type: 'Textarea',
  meta: {
    displayName: 'Textarea',
    icon: 'AlignLeft',
    category: 'input',
    defaultProps: {
      placeholder: 'Enter text...',
      rows: 3,
      disabled: false,
      fullWidth: true,
      resize: 'vertical',
      maxLength: '',
    },
  },
  propSchema: [
    { name: 'placeholder', label: 'Placeholder', section: 'Content', control: 'text', defaultValue: 'Enter text...' },
    { name: 'rows', label: 'Rows', section: 'Content', control: 'number', defaultValue: 3 },
    { name: 'maxLength', label: 'Max Length', section: 'Content', control: 'number', defaultValue: '' },
    { name: 'fullWidth', label: 'Full Width', section: 'Layout', control: 'switch', defaultValue: true },
    { name: 'disabled', label: 'Disabled', section: 'Layout', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On Change' },
    { name: 'onSubmit', label: 'On Submit (Ctrl+Enter)' },
    { name: 'onFocus', label: 'On Focus' },
    { name: 'onBlur', label: 'On Blur' },
  ],
  exposedState: [
    { name: 'value', label: 'Value', defaultValue: '' },
  ],
  Component: TextareaComponent,
  SettingsPanel: TextareaSettings,
})

registerComponent(textareaDefinition)

/* ═══════════════════════════════════════════════════════════════
   Select
   ═══════════════════════════════════════════════════════════════ */

interface SelectCompProps {
  placeholder: string
  options: string
  disabled: boolean
  fullWidth: boolean
  defaultValue: string
}

const SelectComponent = ({ id, props, onEvent }: RendererProps<SelectCompProps>) => {
  const { value, setValue } = useComponentState<string>(id, 'value', props.defaultValue || '')

  // Parse options: "Option 1, Option 2" or "value1:Label 1, value2:Label 2"
  const parsedOptions = (props.options || '').split(',').map((o) => o.trim()).filter(Boolean).map((o) => {
    const parts = o.split(':')
    if (parts.length >= 2) {
      return { value: parts[0].trim(), label: parts.slice(1).join(':').trim() }
    }
    return { value: o, label: o }
  })

  return (
    <select
      value={value || ''}
      disabled={props.disabled}
      onChange={(e) => {
        setValue(e.target.value)
        onEvent?.('onChange', e.target.value)
      }}
      onClick={(e) => e.stopPropagation()}
      className={[
        'flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
        props.fullWidth ? 'w-full' : '',
      ].filter(Boolean).join(' ')}
    >
      {props.placeholder && <option value="" disabled>{props.placeholder}</option>}
      {parsedOptions.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  )
}

function SelectSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="placeholder" label="Placeholder" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="options" label="Options (comma sep)" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="defaultValue" label="Default Value" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Layout">
        <ToolbarItem nodeId={nodeId} propKey="fullWidth" label="Full Width" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
      </ToolbarSection>
    </>
  )
}

const selectDefinition = defineComponent<SelectCompProps>({
  type: 'Select',
  meta: {
    displayName: 'Select',
    icon: 'ChevronDown',
    category: 'input',
    defaultProps: {
      placeholder: 'Select an option...',
      options: 'Option 1, Option 2, Option 3',
      disabled: false,
      fullWidth: true,
      defaultValue: '',
    },
  },
  propSchema: [
    { name: 'placeholder', label: 'Placeholder', section: 'Content', control: 'text', defaultValue: 'Select an option...' },
    { name: 'options', label: 'Options', section: 'Content', control: 'text', defaultValue: 'Option 1, Option 2, Option 3' },
    { name: 'defaultValue', label: 'Default Value', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'fullWidth', label: 'Full Width', section: 'Layout', control: 'switch', defaultValue: true },
    { name: 'disabled', label: 'Disabled', section: 'Layout', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On Change' },
  ],
  exposedState: [
    { name: 'value', label: 'Selected Value', defaultValue: '' },
  ],
  Component: SelectComponent,
  SettingsPanel: SelectSettings,
})

registerComponent(selectDefinition)

/* ═══════════════════════════════════════════════════════════════
   Checkbox
   ═══════════════════════════════════════════════════════════════ */

interface CheckboxCompProps {
  label: string
  defaultChecked: boolean
  disabled: boolean
}

const CheckboxComponent = ({ id, props, onEvent }: RendererProps<CheckboxCompProps>) => {
  const { value: checked, setValue: setChecked } = useComponentState<boolean>(id, 'checked', props.defaultChecked)

  return (
    <label className="flex items-center gap-2 cursor-pointer" onClick={(e) => e.stopPropagation()}>
      <input
        type="checkbox"
        checked={checked}
        disabled={props.disabled}
        onChange={(e) => {
          setChecked(e.target.checked)
          onEvent?.('onChange', e.target.checked)
        }}
        className={[
          'h-4 w-4 rounded border border-primary text-primary focus:ring-primary',
          'disabled:cursor-not-allowed disabled:opacity-50',
        ].filter(Boolean).join(' ')}
      />
      {props.label && (
        <Label className={props.disabled ? 'opacity-50' : ''}>
          {props.label}
        </Label>
      )}
    </label>
  )
}

function CheckboxSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="label" label="Label" type="text" />
      </ToolbarSection>
      <ToolbarSection title="State">
        <ToolbarItem nodeId={nodeId} propKey="defaultChecked" label="Default Checked" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
      </ToolbarSection>
    </>
  )
}

const checkboxDefinition = defineComponent<CheckboxCompProps>({
  type: 'Checkbox',
  meta: {
    displayName: 'Checkbox',
    icon: 'CheckSquare',
    category: 'input',
    defaultProps: {
      label: 'Check me',
      defaultChecked: false,
      disabled: false,
    },
  },
  propSchema: [
    { name: 'label', label: 'Label', section: 'Content', control: 'text', defaultValue: 'Check me' },
    { name: 'defaultChecked', label: 'Default Checked', section: 'State', control: 'switch', defaultValue: false },
    { name: 'disabled', label: 'Disabled', section: 'State', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On Change' },
  ],
  exposedState: [
    { name: 'checked', label: 'Checked', defaultValue: false },
  ],
  Component: CheckboxComponent,
  SettingsPanel: CheckboxSettings,
})

registerComponent(checkboxDefinition)

/* ═══════════════════════════════════════════════════════════════
   Switch
   ═══════════════════════════════════════════════════════════════ */

interface SwitchCompProps {
  label: string
  defaultChecked: boolean
  disabled: boolean
  labelPosition: 'left' | 'right'
}

const SwitchComponent = ({ id, props, onEvent }: RendererProps<SwitchCompProps>) => {
  const { value: checked, setValue: setChecked } = useComponentState<boolean>(id, 'checked', props.defaultChecked)
  const labelEl = props.label ? <Label className={props.disabled ? 'opacity-50' : ''}>{props.label}</Label> : null

  return (
    <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
      {props.labelPosition === 'left' && labelEl}
      <Switch
        checked={checked}
        disabled={props.disabled}
        onCheckedChange={(val) => {
          setChecked(val)
          onEvent?.('onChange', val)
        }}
      />
      {props.labelPosition !== 'left' && labelEl}
    </div>
  )
}

function SwitchSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="label" label="Label" type="text" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="labelPosition"
          label="Label Position"
          type="radio"
          options={[
            { label: 'Left', value: 'left' },
            { label: 'Right', value: 'right' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="State">
        <ToolbarItem nodeId={nodeId} propKey="defaultChecked" label="Default On" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
      </ToolbarSection>
    </>
  )
}

const switchDefinition = defineComponent<SwitchCompProps>({
  type: 'Switch',
  meta: {
    displayName: 'Switch',
    icon: 'ToggleLeft',
    category: 'input',
    defaultProps: {
      label: 'Toggle',
      defaultChecked: false,
      disabled: false,
      labelPosition: 'right',
    },
  },
  propSchema: [
    { name: 'label', label: 'Label', section: 'Content', control: 'text', defaultValue: 'Toggle' },
    { name: 'labelPosition', label: 'Label Position', section: 'Content', control: 'select', defaultValue: 'right', options: [{ label: 'Left', value: 'left' }, { label: 'Right', value: 'right' }] },
    { name: 'defaultChecked', label: 'Default On', section: 'State', control: 'switch', defaultValue: false },
    { name: 'disabled', label: 'Disabled', section: 'State', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On Change' },
  ],
  exposedState: [
    { name: 'checked', label: 'Checked', defaultValue: false },
  ],
  Component: SwitchComponent,
  SettingsPanel: SwitchSettings,
})

registerComponent(switchDefinition)

/* ═══════════════════════════════════════════════════════════════
   Label
   ═══════════════════════════════════════════════════════════════ */

interface LabelCompProps {
  text: string
  fontSize: string
  fontWeight: string
  color: string
  required: boolean
}

const LabelComponent = ({ props }: RendererProps<LabelCompProps>) => {
  return (
    <Label
      style={{
        fontSize: props.fontSize ? `${props.fontSize}px` : undefined,
        fontWeight: props.fontWeight || undefined,
        color: props.color || undefined,
      }}
    >
      {props.text}
      {props.required && <span className="text-destructive ml-0.5">*</span>}
    </Label>
  )
}

function LabelSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="text" label="Text" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="required" label="Show Required" type="switch" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="fontSize" label="Font Size" type="slider" min={8} max={24} />
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
        <ToolbarItem nodeId={nodeId} propKey="color" label="Color" type="color" />
      </ToolbarSection>
    </>
  )
}

const labelDefinition = defineComponent<LabelCompProps>({
  type: 'Label',
  meta: {
    displayName: 'Label',
    icon: 'CaseSensitive',
    category: 'content',
    defaultProps: {
      text: 'Label',
      fontSize: '',
      fontWeight: '500',
      color: '',
      required: false,
    },
  },
  propSchema: [
    { name: 'text', label: 'Text', section: 'Content', control: 'text', defaultValue: 'Label' },
    { name: 'required', label: 'Show Required', section: 'Content', control: 'switch', defaultValue: false },
    { name: 'fontSize', label: 'Font Size', section: 'Style', control: 'number', defaultValue: '' },
    { name: 'fontWeight', label: 'Weight', section: 'Style', control: 'select', defaultValue: '500', options: [{ label: 'Normal', value: '400' }, { label: 'Medium', value: '500' }, { label: 'Semibold', value: '600' }, { label: 'Bold', value: '700' }] },
    { name: 'color', label: 'Color', section: 'Style', control: 'color', defaultValue: '' },
  ],
  eventSchema: [],
  exposedState: [],
  Component: LabelComponent,
  SettingsPanel: LabelSettings,
})

registerComponent(labelDefinition)

/* ═══════════════════════════════════════════════════════════════
   FileUpload
   ═══════════════════════════════════════════════════════════════ */

interface FileUploadCompProps {
  label: string
  accept: string
  multiple: boolean
  disabled: boolean
  maxSize: string
}

const FileUploadComponent = ({ id, props, onEvent }: RendererProps<FileUploadCompProps>) => {
  const runtimeFiles = useRuntimeStateStore((s) => s.componentState[id]?.files as string[] | undefined)
  const setComponentState = useRuntimeStateStore((s) => s.setComponentState)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files
    if (!fileList) return
    const names = Array.from(fileList).map((f) => f.name)
    setComponentState(id, 'files', names)
    setComponentState(id, 'fileCount', fileList.length)
    onEvent?.('onChange', { files: names, count: fileList.length })
  }

  return (
    <label
      className={cn(
        'flex flex-col items-center justify-center w-full min-h-[120px] rounded-lg border-2 border-dashed border-border bg-muted/30 cursor-pointer transition-colors',
        'hover:bg-muted/50 hover:border-muted-foreground/30',
        props.disabled && 'opacity-50 pointer-events-none',
      )}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex flex-col items-center gap-2 py-4 px-6">
        <Upload size={24} className="text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">
          {props.label}
        </span>
        {runtimeFiles && runtimeFiles.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {runtimeFiles.length} file{runtimeFiles.length > 1 ? 's' : ''} selected
          </span>
        ) : (
          props.accept && (
            <span className="text-xs text-muted-foreground">
              {props.accept}
            </span>
          )
        )}
        {props.maxSize && (
          <span className="text-[10px] text-muted-foreground/70">
            Max {props.maxSize}MB
          </span>
        )}
      </div>
      <input
        type="file"
        className="hidden"
        accept={props.accept || undefined}
        multiple={props.multiple}
        disabled={props.disabled}
        onChange={handleChange}
      />
    </label>
  )
}

function FileUploadSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="label" label="Label" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="accept" label="Accept" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="maxSize" label="Max Size (MB)" type="number" />
      </ToolbarSection>
      <ToolbarSection title="Options">
        <ToolbarItem nodeId={nodeId} propKey="multiple" label="Multiple" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="disabled" label="Disabled" type="switch" />
      </ToolbarSection>
    </>
  )
}

const fileUploadDefinition = defineComponent<FileUploadCompProps>({
  type: 'FileUpload',
  meta: {
    displayName: 'File Upload',
    icon: 'Upload',
    category: 'input',
    defaultProps: {
      label: 'Click to upload',
      accept: '',
      multiple: false,
      disabled: false,
      maxSize: '',
    },
  },
  propSchema: [
    { name: 'label', label: 'Label', section: 'Content', control: 'text', defaultValue: 'Click to upload' },
    { name: 'accept', label: 'Accept', section: 'Content', control: 'text', defaultValue: '' },
    { name: 'maxSize', label: 'Max Size (MB)', section: 'Content', control: 'number', defaultValue: '' },
    { name: 'multiple', label: 'Multiple', section: 'Options', control: 'switch', defaultValue: false },
    { name: 'disabled', label: 'Disabled', section: 'Options', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On File Select' },
  ],
  exposedState: [
    { name: 'files', label: 'File Names', defaultValue: [] },
    { name: 'fileCount', label: 'File Count', defaultValue: 0 },
  ],
  Component: FileUploadComponent,
  SettingsPanel: FileUploadSettings,
})

registerComponent(fileUploadDefinition)
