/**
 * The glass design-system layer. Styles: ./glass.css (imported once from src/index.css).
 * Use `cx` (not `cn`) when composing glass classes: tailwind-merge can drop custom utilities.
 */
export { cx, STEP_STATUSES, STATUS_LABEL, type StepStatus, type Tone } from './cx'

// Page family (the editor's Page view and the Notebook)
export { PageDoc, type PageDocProps } from './page/PageDoc'
export { PageSection, type PageSectionProps } from './page/PageSection'
export { PageStep, StepResult, PageRail, type PageStepProps, type StepResultProps } from './page/PageStep'
export { PageBranch, type PageBranchProps, type BranchCase } from './page/PageBranch'
export { PageLoop, type PageLoopProps, type LoopIteration } from './page/PageLoop'
export { PageParallel, type PageParallelProps, type ParallelLane } from './page/PageParallel'
export { PageForEach, type PageForEachProps, type ForEachRow } from './page/PageForEach'

// controls
export { StatusMark, type StatusMarkProps } from './StatusMark'
export { KindLabel, type KindLabelProps } from './KindLabel'
export { ValueToken, type ValueTokenProps } from './ValueToken'
export { Chip, type ChipProps } from './Chip'
export { ViewSwitch, type ViewSwitchProps, type ViewSwitchOption } from './ViewSwitch'
export { SplitButton, type SplitButtonProps, type SplitButtonItem } from './SplitButton'
export { Palette, filterPalette, type PaletteProps, type PaletteItem } from './Palette'

// surfaces
export { GlassPanel, type GlassPanelProps } from './GlassPanel'
export { GlassSheet, type GlassSheetProps, type GlassSheetPane } from './GlassSheet'
export { Drawer, type DrawerProps, type DrawerTab } from './Drawer'

// content
export { DiffBlock, type DiffBlockProps, type DiffLine } from './DiffBlock'
export { CheckStrip, summarizeChecks, type CheckStripProps, type CheckStripItem, type CheckResult } from './CheckStrip'
export { DataTablePreview, formatCell, type DataTablePreviewProps } from './DataTablePreview'
