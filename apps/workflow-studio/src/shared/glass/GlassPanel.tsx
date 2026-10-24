import { forwardRef } from 'react'
import { cx } from './cx'

export interface GlassPanelProps extends React.HTMLAttributes<HTMLElement> {
  /** `pane` = blurred outer glass (default) · `well` = inner recessed glass · `raised` = inner raised glass. */
  variant?: 'pane' | 'well' | 'raised'
  padding?: 'none' | 'sm' | 'md' | 'lg'
  as?: 'div' | 'section' | 'aside' | 'article' | 'header' | 'footer' | 'nav'
}

/** A glass surface: border 0, 1px ring, sheen, inset highlight, two-layer depth. */
export const GlassPanel = forwardRef<HTMLElement, GlassPanelProps>(function GlassPanel(
  { variant = 'pane', padding = 'md', as: Tag = 'div', className, ...rest },
  ref,
) {
  return (
    <Tag
      ref={ref as React.Ref<HTMLDivElement>}
      data-variant={variant === 'pane' ? undefined : variant}
      data-pad={padding === 'none' ? undefined : padding}
      className={cx('gl-panel', className)}
      {...rest}
    />
  )
})
