/** Every section of the nteract gallery: one live example per registry item, file and widget control. */
import { type ComponentType, type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { BookOpen, Bold, Calendar, ChevronDown, Code2, Italic, Settings, Sparkles, Underline, Users } from 'lucide-react'

// outputs
import { AnsiErrorOutput, AnsiOutput, AnsiStreamOutput } from '@/components/outputs/ansi-output'
import { ImageOutput } from '@/components/outputs/image-output'
import { JsonOutput } from '@/components/outputs/json-output'
import { MediaProvider } from '@/components/outputs/media-provider'
import { DEFAULT_PRIORITY, MediaRouter, getSelectedMimeType, type RendererProps } from '@/components/outputs/media-router'
import { SvgOutput } from '@/components/outputs/svg-output'
// cell
import { CellBetweener } from '@/components/cell/CellBetweener'
import { CellContainer } from '@/components/cell/CellContainer'
import { CellControls } from '@/components/cell/CellControls'
import { CellHeader } from '@/components/cell/CellHeader'
import { AiCellButton, CellTypeButton, CodeCellButton, MarkdownCellButton, SqlCellButton, type CellType } from '@/components/cell/CellTypeButton'
import { CellTypeSelector } from '@/components/cell/CellTypeSelector'
import { CollaboratorAvatars } from '@/components/cell/CollaboratorAvatars'
import { ExecutionCount } from '@/components/cell/ExecutionCount'
import { ExecutionStatus } from '@/components/cell/ExecutionStatus'
import { GlobalFindBar } from '@/components/cell/GlobalFindBar'
import { defaultGutterColors } from '@/components/cell/gutter-colors'
import { OutputArea, type JupyterOutput } from '@/components/cell/OutputArea'
import { PlayButton } from '@/components/cell/PlayButton'
import { PresenceBookmarks } from '@/components/cell/PresenceBookmarks'
import { RuntimeHealthIndicator, type RuntimeStatus } from '@/components/cell/RuntimeHealthIndicator'
// editor
import { CodeMirrorEditor, detectCellMagic, languageDisplayNames, type SupportedLanguage } from '@/components/editor'
// icons + ui
import { CondaIcon, DenoIcon, PixiIcon, PythonIcon, UvIcon } from '@/components/icons/runtime-icons'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Avatar, AvatarBadge, AvatarFallback, AvatarGroup, AvatarGroupCount, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator, CommandShortcut } from '@/components/ui/command'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogPortal, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Dialog as DialogPrimitive } from 'radix-ui'
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuShortcut, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from '@/components/ui/popover'
import { Progress } from '@/components/ui/progress'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select'
import { BRAND_COLORS, PageDots, SelectionCard } from '@/components/ui/selection-card'
import { Slider } from '@/components/ui/slider'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Toggle } from '@/components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
// isolation
import { IsolatedFrame, IsolationTest, type IsolatedFrameHandle } from '@/components/isolated'
// widgets
import { WidgetView } from '@/components/widgets/widget-view'
import { useWidgetModel, useWidgetModels, useWidgetStoreRequired } from '@/components/widgets/widget-store-context'
// lib
import { useDarkMode } from '@/lib/dark-mode'
import { ErrorBoundary } from '@/lib/error-boundary'
import { highlightTextInDom } from '@/lib/highlight-text'
import { OutputErrorFallback } from '@/lib/output-error-fallback'
import { WidgetErrorFallback } from '@/lib/widget-error-fallback'

import { markRendered, type RegistryItem } from './coverage'
import type { Kernel } from './kernel'
import * as S from './samples'

export type Section = {
  id: string
  title: string
  category: string
  desc: string
  imports: string
  registry: RegistryItem[]
  /** files (relative to src/components, or lib/…) this section exercises */
  files: string[]
  Example: ComponentType
}

/* ── small helpers ─────────────────────────────────────────────── */
const Row = ({ children, wrap = true }: { children: ReactNode; wrap?: boolean }) => <div className={`gx-row${wrap ? '' : ' gx-row--nowrap'}`}>{children}</div>
const Cap = ({ children }: { children: ReactNode }) => <div className="gx-cap">{children}</div>
const Tile = ({ label, children, wide }: { label: ReactNode; children: ReactNode; wide?: boolean }) => (
  <div className={`gx-tile${wide ? ' gx-tile--wide' : ''}`}><div className="gx-tile__h">{label}</div><div className="gx-tile__b">{children}</div></div>
)
const Grid = ({ children, min = 240 }: { children: ReactNode; min?: number }) => <div className="gx-grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))` }}>{children}</div>

/** A widget rendered by model id through WidgetView, labelled with its model name (reports coverage on mount). */
function W({ id, note, wide }: { id: string; note?: string; wide?: boolean }) {
  const model = useWidgetModel(id)
  useEffect(() => { if (model) markRendered([model.modelName]) }, [model])
  return (
    <div className={`gx-tile${wide ? ' gx-tile--wide' : ''}`} data-gallery-model={model?.modelName}>
      <div className="gx-tile__h"><code>{model?.modelName ?? '…'}</code>{note && <span>{note}</span>}</div>
      <div className="gx-tile__b"><WidgetView modelId={id} /></div>
    </div>
  )
}

const KernelCtx = { current: null as Kernel | null }
export const setGalleryKernel = (k: Kernel | null) => { KernelCtx.current = k }

/* ── Outputs ───────────────────────────────────────────────────── */
function AnsiExample() {
  return (
    <div className="gx-stack">
      <Cap>AnsiOutput — any ANSI-coloured text</Cap>
      <AnsiOutput>{S.ANSI_LOG}</AnsiOutput>
      <Cap>AnsiStreamOutput — stdout / stderr</Cap>
      <AnsiStreamOutput streamName="stdout" text={'Read 12,408 rows in 0.21s\n'} />
      <AnsiStreamOutput streamName="stderr" text={String((S.STREAM_STDERR as { text: string }).text)} />
      <Cap>AnsiErrorOutput — an IPython traceback</Cap>
      <AnsiErrorOutput ename="KeyError" evalue="'revenue'" traceback={S.TRACEBACK} />
    </div>
  )
}
function ImageExample() {
  return (
    <Grid min={170}>
      <Tile label="image/png"><ImageOutput data={S.PNG} mediaType="image/png" alt="PNG chart" width={160} /></Tile>
      <Tile label="image/jpeg"><ImageOutput data={S.JPEG} mediaType="image/jpeg" alt="JPEG photo" width={160} /></Tile>
      <Tile label="image/gif (animated)"><ImageOutput data={S.GIF} mediaType="image/gif" alt="GIF" width={80} height={80} /></Tile>
      <Tile label="image/webp"><ImageOutput data={S.WEBP} mediaType="image/webp" alt="WebP tile" width={160} /></Tile>
    </Grid>
  )
}
const HtmlExample = () => (
  <div className="gx-stack">
    <Cap>HtmlOutput refuses to render in the main page (it throws outside an iframe), so it runs where it belongs: inside an IsolatedFrame, via OutputArea. A pandas DataFrame:</Cap>
    <OutputArea outputs={[S.RESULT_HTML]} />
    <Cap>Rich HTML with inline styles and a script, sandboxed:</Cap>
    <OutputArea outputs={[{ output_type: 'display_data', data: { 'text/html': '<div style="display:flex;gap:10px;font:13px system-ui"><span style="padding:4px 10px;border-radius:99px;background:#dcfce7;color:#166534">passed 41</span><span style="padding:4px 10px;border-radius:99px;background:#fee2e2;color:#991b1b">failed 2</span><span id=t style="opacity:.7"></span></div><script>document.getElementById("t").textContent="rendered at "+new Date().toLocaleTimeString()</script>' } }]} />
  </div>
)
const SvgExample = () => <SvgOutput data={S.SVG_CHART} />
const MarkdownExample = () => (
  <div className="gx-stack">
    <Cap>MarkdownOutput also requires an iframe; OutputArea routes text/markdown to an IsolatedFrame, where it renders with tables, task lists, KaTeX and copyable code:</Cap>
    <OutputArea outputs={[S.DISPLAY_MD]} />
  </div>
)
function JsonExample() {
  return (
    <div className="gx-two">
      <Tile label="expanded"><JsonOutput data={S.JSON_DATA} /></Tile>
      <Tile label="collapsed={1} · displayDataTypes"><JsonOutput data={S.JSON_DATA} collapsed={1} displayDataTypes /></Tile>
    </div>
  )
}

const Sparkline = ({ data }: RendererProps) => {
  const pts = (data as { values: number[] }).values
  const max = Math.max(...pts)
  return (
    <svg width="220" height="48" className="gx-spark" role="img" aria-label="sparkline">
      <polyline fill="none" stroke="currentColor" strokeWidth="2" points={pts.map((v, i) => `${(i * 220) / (pts.length - 1)},${46 - (v / max) * 42}`).join(' ')} />
    </svg>
  )
}
function MediaProviderExample() {
  const bundle = { 'application/vnd.gallery.sparkline+json': { values: [3, 5, 4, 8, 6, 9, 12, 10, 14] }, 'text/plain': '[3, 5, 4, 8, …]' }
  return (
    <div className="gx-stack">
      <Cap>A MediaProvider registers a custom renderer for <code>application/vnd.gallery.sparkline+json</code>; every MediaRouter below it uses it.</Cap>
      <MediaProvider renderers={{ 'application/vnd.gallery.sparkline+json': Sparkline }} priority={['application/vnd.gallery.sparkline+json', ...DEFAULT_PRIORITY]}>
        <Tile label="inside the provider → custom renderer"><MediaRouter data={bundle} /></Tile>
      </MediaProvider>
      <Tile label="outside the provider → falls back to text/plain"><MediaRouter data={bundle} /></Tile>
    </div>
  )
}
function MediaRouterExample() {
  const rows: [string, Record<string, unknown>, string?][] = [
    ['text/plain (ANSI)', { 'text/plain': S.ANSI_LOG }],
    ['application/json', { 'application/json': S.JSON_DATA }],
    ['image/png', { 'image/png': S.PNG }],
    ['image/jpeg', { 'image/jpeg': S.JPEG }],
    ['image/gif', { 'image/gif': S.GIF }],
    ['image/webp', { 'image/webp': S.WEBP }],
    ['application/vnd.vegalite.v5+json (no renderer → JSON)', { 'application/vnd.vegalite.v5+json': { mark: 'bar', encoding: { x: { field: 'region' } } } }],
    ['application/vnd.jupyter.widget-view+json (in-DOM)', { 'application/vnd.jupyter.widget-view+json': { model_id: 'int_slider', version_major: 2, version_minor: 0 } }],
    ['bundle {png, text/plain} → picks png', { 'image/png': S.PNG, 'text/plain': '<Figure>' }],
    ['same bundle, priority=["text/plain"]', { 'image/png': S.PNG, 'text/plain': '<Figure size 320x160 with 1 Axes>' }, 'plain'],
  ]
  return (
    <div className="gx-stack">
      <Grid min={300}>
        {rows.map(([label, data, p]) => {
          const priority = p ? ['text/plain'] : undefined
          return (
            <Tile key={label} label={<>{label}<span className="gx-mime">→ {getSelectedMimeType(data, priority) ?? '—'}</span></>}>
              <MediaRouter data={data} priority={priority} metadata={{ 'image/png': { width: 200 }, 'image/jpeg': { width: 200 }, 'image/webp': { width: 200 } }} />
            </Tile>
          )
        })}
        <Tile label={<>custom renderers prop<span className="gx-mime">→ text/csv</span></>}>
          <MediaRouter data={{ 'text/csv': 'region,mrr\nNA,1102400\nEMEA,412880' }} renderers={{ 'text/csv': ({ data }) => <table className="gx-csv"><tbody>{String(data).split('\n').map((r, i) => <tr key={i}>{r.split(',').map((c, j) => i ? <td key={j}>{c}</td> : <th key={j}>{c}</th>)}</tr>)}</tbody></table> }} />
        </Tile>
        <Tile label={<>unknown MIME + fallback prop<span className="gx-mime">→ fallback</span></>}>
          <MediaRouter data={{}} fallback={<span className="gx-muted">No renderable MIME type in this bundle.</span>} />
        </Tile>
      </Grid>
      <Cap>text/html, text/markdown and image/svg+xml are refused in the main DOM by MediaRouter's isolation guard; OutputArea sends them to an IsolatedFrame, where the same MediaRouter renders them:</Cap>
      <OutputArea outputs={[S.RESULT_HTML, S.DISPLAY_MD, S.DISPLAY_SVG]} />
    </div>
  )
}

function OutputAreaExample() {
  const [collapsed, setCollapsed] = useState(false)
  const [collapsed2, setCollapsed2] = useState(true)
  return (
    <div className="gx-stack">
      <Cap>In-DOM outputs: stream (stdout + stderr), error, execute_result (text/plain, application/json) and display_data (image/png)</Cap>
      <OutputArea outputs={[S.STREAM_STDOUT, S.STREAM_STDERR, S.RESULT_PLAIN, S.RESULT_JSON, S.DISPLAY_PNG, S.ERROR_OUT]} collapsed={collapsed} onToggleCollapse={() => setCollapsed(c => !c)} />
      <div className="gx-two">
        <Tile label="long stream, maxHeight=180 (scrolls)"><OutputArea outputs={[S.LONG_STREAM]} maxHeight={180} /></Tile>
        <Tile label="collapsed (click to expand)"><OutputArea outputs={[S.STREAM_STDOUT, S.RESULT_PLAIN]} collapsed={collapsed2} onToggleCollapse={() => setCollapsed2(c => !c)} /></Tile>
        <Tile label="error only"><OutputArea outputs={[{ output_type: 'error', ename: 'ZeroDivisionError', evalue: 'division by zero', traceback: ['\u001b[0;31mZeroDivisionError\u001b[0m: division by zero'] }]} /></Tile>
        <Tile label="empty outputs → renders nothing"><OutputArea outputs={[]} /><span className="gx-muted">(nothing rendered)</span></Tile>
      </div>
      <Cap>Isolated outputs (iframe): execute_result text/html, display_data text/markdown + image/svg+xml, and a widget-view+json display_data whose IntSlider is the same model as the one in the Sliders section — drag either</Cap>
      <OutputArea outputs={[S.RESULT_HTML, S.DISPLAY_SVG, S.DISPLAY_WIDGET]} />
    </div>
  )
}

/* ── Cells ─────────────────────────────────────────────────────── */
type RunState = 'idle' | 'queued' | 'running' | 'completed' | 'error'
type NbCell = { id: string; type: CellType; source: string; outputs: JupyterOutput[]; count: number | null; state: RunState; showSource: boolean }
const LANG: Record<CellType, SupportedLanguage> = { code: 'python', markdown: 'markdown', sql: 'sql', ai: 'markdown' }

function NotebookExample() {
  const dark = useDarkMode()
  const [cells, setCells] = useState<NbCell[]>([
    { id: 'c1', type: 'markdown', source: '## Churn by region\nRun the cells below to refresh.', outputs: [], count: null, state: 'idle', showSource: true },
    { id: 'c2', type: 'code', source: S.CODE.python, outputs: [{ output_type: 'stream', name: 'stdout', text: 'churn: 2.14%\n' }], count: 3, state: 'completed', showSource: true },
    { id: 'c3', type: 'sql', source: S.CODE.sql, outputs: [S.RESULT_PLAIN], count: 4, state: 'idle', showSource: true },
  ])
  const [focus, setFocus] = useState('c2')
  const [counter, setCounter] = useState(5)
  const patch = (id: string, p: Partial<NbCell>) => setCells(cs => cs.map(c => (c.id === id ? { ...c, ...p } : c)))
  const run = (c: NbCell) => {
    patch(c.id, { state: 'queued' })
    setTimeout(() => patch(c.id, { state: 'running' }), 300)
    setTimeout(() => {
      const n = counter
      setCounter(n + 1)
      patch(c.id, { state: 'completed', count: n, outputs: c.type === 'markdown' ? [] : [{ output_type: 'stream', name: 'stdout', text: `run #${n} finished at ${new Date().toLocaleTimeString()}\n` }, ...(c.type === 'sql' ? [S.RESULT_PLAIN] : [])] })
    }, 1300)
  }
  const add = (after: number, type: CellType) => {
    const id = `c${Date.now()}`
    setCells(cs => [...cs.slice(0, after + 1), { id, type, source: type === 'sql' ? 'SELECT 1;' : type === 'markdown' ? '*New note*' : 'print("hello")', outputs: [], count: null, state: 'idle', showSource: true }, ...cs.slice(after + 1)])
    setFocus(id)
  }
  const move = (i: number, d: number) => setCells(cs => { const n = [...cs]; const [c] = n.splice(i, 1); n.splice(Math.max(0, Math.min(n.length, i + d)), 0, c); return n })
  return (
    <div className="gx-notebook">
      {cells.map((c, i) => (
        <div key={c.id}>
          <CellContainer
            id={c.id}
            cellType={c.type}
            isFocused={focus === c.id}
            onFocus={() => setFocus(c.id)}
            gutterContent={<PlayButton executionState={c.state} cellType={c.type} isFocused={focus === c.id} onExecute={() => run(c)} onInterrupt={() => patch(c.id, { state: 'idle' })} gutterMode />}
            codeContent={
              <div className="gx-stack gx-stack--tight">
                <CellHeader
                  leftContent={<div className="gx-row gx-row--nowrap"><ExecutionCount count={c.count} isExecuting={c.state === 'running'} /><CellTypeSelector currentType={c.type} onTypeChange={t => patch(c.id, { type: t })} /></div>}
                  rightContent={<div className="gx-row gx-row--nowrap"><ExecutionStatus executionState={c.state} />{i === 1 && <PresenceBookmarks users={S.USERS.slice(0, 2)} />}<CellControls sourceVisible={c.showSource} toggleSourceVisibility={() => patch(c.id, { showSource: !c.showSource })} hasOutputs={c.outputs.length > 0} onClearOutputs={() => patch(c.id, { outputs: [] })} onDeleteCell={() => setCells(cs => cs.filter(x => x.id !== c.id))} onMoveUp={() => move(i, -1)} onMoveDown={() => move(i, 1)} onMoveToTop={() => move(i, -99)} onMoveToBottom={() => move(i, 99)} canMoveUp={i > 0} canMoveDown={i < cells.length - 1} hasCellsBelow={i < cells.length - 1} onDeleteAllBelow={() => setCells(cs => cs.slice(0, i + 1))} forceVisible={focus === c.id} /></div>}
                />
                {c.showSource && <CodeMirrorEditor value={c.source} language={LANG[c.type]} onValueChange={v => patch(c.id, { source: v })} theme={dark ? 'dark' : 'light'} />}
              </div>
            }
            outputContent={c.outputs.length ? <OutputArea outputs={c.outputs} /> : undefined}
          />
          <CellBetweener cellType={c.type}>
            <div className="gx-between"><CodeCellButton size="xs" showPlus onClick={() => add(i, 'code')} /><MarkdownCellButton size="xs" showPlus onClick={() => add(i, 'markdown')} /><SqlCellButton size="xs" showPlus onClick={() => add(i, 'sql')} /></div>
          </CellBetweener>
        </div>
      ))}
    </div>
  )
}

function CellContainerExample() {
  const [focus, setFocus] = useState<string>('code')
  return (
    <div className="gx-stack gx-clip">
      <Cap>Every gutter colour set in gutter-colors.ts ({Object.keys(defaultGutterColors).join(', ')}). Click a cell to focus it.</Cap>
      <div className="gx-two">
        <b className="gx-colhead">unfocused</b><b className="gx-colhead">focused</b>
        {Object.keys(defaultGutterColors).map(t => [false, true].map(f => (
          <CellContainer key={`${t}-${f}`} id={`${t}-${f}`} cellType={t} isFocused={f || focus === `${t}-${f}`} onFocus={() => setFocus(`${t}-${f}`)} codeContent={<div className="gx-mono">{t} cell source</div>} outputContent={<div className="gx-muted">{t} output</div>} />
        )))}
      </div>
      <Cap>Without outputContent (code only), and with hideOutput:</Cap>
      <div className="gx-two">
        <CellContainer id="code-only" cellType="code" isFocused codeContent={<div className="gx-mono">x = 1</div>} />
        <CellContainer id="hide-out" cellType="code" hideOutput codeContent={<div className="gx-mono">train(model)  # output hidden</div>} outputContent={<div>hidden</div>} />
      </div>
      <Cap>Unsegmented (children only) with a custom gutter colour:</Cap>
      <CellContainer id="custom" cellType="brand" customGutterColors={{ brand: { ribbon: { default: 'bg-fuchsia-300', focused: 'bg-fuchsia-600' }, outputRibbon: { default: 'bg-fuchsia-200', focused: 'bg-fuchsia-400' }, background: { focused: 'bg-fuchsia-50/40' } } }} isFocused={focus === 'custom'} onFocus={() => setFocus('custom')}>
        <div className="gx-pad">A custom <code>brand</code> cell type</div>
      </CellContainer>
    </div>
  )
}
function CellBetweenerExample() {
  return (
    <div className="gx-clip">
      <CellContainer id="b1" cellType="code" codeContent={<div className="gx-mono">df = load("orders")</div>} />
      <CellBetweener cellType="code"><div className="gx-between"><CellTypeButton cellType="code" size="xs" showPlus /><CellTypeButton cellType="ai" size="xs" showPlus label="Ask AI" /></div></CellBetweener>
      <CellContainer id="b2" cellType="code" codeContent={<div className="gx-mono">df.describe()</div>} />
      <CellBetweener cellType="markdown" />
      <CellContainer id="b3" cellType="markdown" codeContent={<div>Notes on the data</div>} />
    </div>
  )
}
function CellHeaderExample() {
  return (
    <div className="gx-stack">
      <CellHeader draggable onDragStart={() => {}} leftContent={<div className="gx-row gx-row--nowrap"><ExecutionCount count={12} /><Badge variant="outline">python</Badge></div>} rightContent={<div className="gx-row gx-row--nowrap"><ExecutionStatus executionState="completed" /><span className="gx-muted">0.84s</span></div>} />
      <CellHeader leftContent={<span className="gx-muted">untitled.sql</span>} rightContent={<ExecutionStatus executionState="running" />} />
    </div>
  )
}
function CellControlsExample() {
  const [visible, setVisible] = useState(true)
  const [ai, setAi] = useState(false)
  const [log, setLog] = useState('Open the ⋯ menu or toggle source visibility.')
  return (
    <div className="gx-stack">
      <Row>
        <CellControls forceVisible sourceVisible={visible} toggleSourceVisibility={() => { setVisible(v => !v); setLog(`source ${visible ? 'hidden' : 'shown'}`) }} hasOutputs onClearOutputs={() => setLog('cleared outputs')} onDeleteCell={() => setLog('deleted cell')} onMoveUp={() => setLog('moved up')} onMoveDown={() => setLog('moved down')} onMoveToTop={() => setLog('moved to top')} onMoveToBottom={() => setLog('moved to bottom')} canMoveUp canMoveDown hasCellsBelow onDeleteAllBelow={() => setLog('deleted all below')} aiContextVisible={ai} toggleAiContextVisibility={() => setAi(a => !a)} playButton={<PlayButton executionState="idle" cellType="code" onExecute={() => setLog('executed')} onInterrupt={() => {}} />} />
      </Row>
      <span className="gx-log">{log}</span>
    </div>
  )
}
function CellTypeButtonExample() {
  return (
    <div className="gx-stack">
      <Row><CodeCellButton showPlus /><MarkdownCellButton showPlus /><SqlCellButton showPlus /><AiCellButton showPlus /></Row>
      <Row><CellTypeButton cellType="code" size="xs" /><CellTypeButton cellType="markdown" size="xs" showIcon={false} label="Text" /><CellTypeButton cellType="sql" size="sm" /><CellTypeButton cellType="ai" size="lg" label="Ask AI" /></Row>
    </div>
  )
}
function CellTypeSelectorExample() {
  const [t, setT] = useState<CellType>('code')
  const [t2, setT2] = useState<CellType>('sql')
  return <Row><CellTypeSelector currentType={t} onTypeChange={setT} /><span className="gx-muted">current: {t}</span><CellTypeSelector currentType={t2} onTypeChange={setT2} enabledTypes={['code', 'sql']} /><span className="gx-muted">enabledTypes=[code, sql]</span></Row>
}
const ExecutionCountExample = () => <Row>{([['null (never run)', null, false], ['executing → *', 8, true], ['12', 12, false], ['999', 999, false]] as const).map(([l, c, x]) => <Tile key={l} label={l}><ExecutionCount count={c} isExecuting={x} /></Tile>)}</Row>
const ExecutionStatusExample = () => <Row>{['idle', 'queued', 'running', 'completed', 'error'].map(s => <Tile key={s} label={s === 'completed' ? 'completed (success)' : s}><ExecutionStatus executionState={s} />{s === 'idle' && <span className="gx-muted">renders nothing when idle</span>}</Tile>)}</Row>
function PlayButtonExample() {
  const [state, setState] = useState<RunState>('idle')
  const run = () => { setState('queued'); setTimeout(() => setState('running'), 400); setTimeout(() => setState('completed'), 1800) }
  return (
    <div className="gx-stack">
      <Row>{(['idle', 'queued', 'running', 'completed', 'error'] as const).map(s => <Tile key={s} label={s}><PlayButton executionState={s} cellType="code" onExecute={() => {}} onInterrupt={() => {}} /></Tile>)}<Tile label="auto-launching"><PlayButton executionState="idle" cellType="code" isAutoLaunching onExecute={() => {}} onInterrupt={() => {}} /></Tile></Row>
      <Row><PlayButton executionState={state} cellType="code" isFocused onExecute={run} onInterrupt={() => setState('idle')} /><span className="gx-log">live: {state} — click to run, click again to interrupt</span></Row>
    </div>
  )
}
function RuntimeHealthExample() {
  const all: RuntimeStatus[] = ['idle', 'busy', 'connecting', 'disconnected']
  const [i, setI] = useState(0)
  return (
    <div className="gx-stack">
      <Row>{all.map(s => <RuntimeHealthIndicator key={s} status={s} kernelName="python3" showStatus />)}</Row>
      <Row><RuntimeHealthIndicator status={all[i]} kernelName="deno" showStatus onClick={() => setI(n => (n + 1) % all.length)} /><span className="gx-muted">clickable — cycles status</span><RuntimeHealthIndicator status="busy" /><span className="gx-muted">dot only</span></Row>
    </div>
  )
}
const withPics = S.USERS.map((u, i) => ({ ...u, picture: i % 2 ? undefined : S.avatarSrc(u.name.split(' ').map(p => p[0]).join(''), u.color) }))
function CollaboratorsExample() {
  const sizes = ['sm', 'default', 'lg'] as const
  return (
    <div className="gx-stack">
      <div className="gx-matrix" style={{ ['--cols' as string]: 3 }}>
        <span />{sizes.map(z => <b key={z}>size={z}</b>)}
        {([[1, '1 user'], [3, '3 users'], [8, '8 users (+N overflow)']] as const).map(([n, l]) => (
          <div key={n} className="gx-matrix__row"><span>{l}</span>{sizes.map(z => <div key={z}><CollaboratorAvatars users={withPics.slice(0, n)} size={z} /></div>)}</div>
        ))}
      </div>
      <Row>
        <Tile label="images (picture set)"><CollaboratorAvatars users={withPics.filter(u => u.picture)} limit={4} size="default" /></Tile>
        <Tile label="initials fallback (no picture)"><CollaboratorAvatars users={S.USERS.slice(0, 4)} limit={4} size="default" /></Tile>
        <Tile label="limit=5 of 8"><CollaboratorAvatars users={withPics} limit={5} /></Tile>
        <Tile label="currentUserId hides yourself"><CollaboratorAvatars users={withPics.slice(0, 3)} currentUserId="u1" size="default" /></Tile>
      </Row>
    </div>
  )
}
function PresenceExample() {
  return (
    <div className="gx-stack">
      <Row>
        <Tile label="1 user"><PresenceBookmarks users={withPics.slice(0, 1)} /></Tile>
        <Tile label="4 users, images + initials"><PresenceBookmarks users={withPics.slice(0, 4)} limit={4} /></Tile>
        <Tile label="8 users, limit=3 (over the limit)"><PresenceBookmarks users={withPics} limit={3} /></Tile>
        <Tile label="no colour → neutral ring"><PresenceBookmarks users={[{ id: 'x', name: 'Guest User' }]} /></Tile>
        <Tile label="custom hover content"><PresenceBookmarks users={withPics.slice(1, 3)} renderUserContent={u => <div className="gx-stack gx-stack--tight"><b>{u.name}</b><span className="gx-muted">editing cell 2 · 30s ago</span></div>} /></Tile>
      </Row>
      <span className="gx-muted">Hover an avatar for its card.</span>
    </div>
  )
}
function FindBarExample() {
  const [q, setQ] = useState('region')
  const [count, setCount] = useState(0)
  const [idx, setIdx] = useState(0)
  const [open, setOpen] = useState(true)
  return (
    <div className="gx-stack">
      {open ? <GlobalFindBar query={q} matchCount={count} currentMatchIndex={count ? idx % count : 0} onQueryChange={v => { setQ(v); setIdx(0) }} onNextMatch={() => setIdx(i => i + 1)} onPrevMatch={() => setIdx(i => (i - 1 + Math.max(count, 1)) % Math.max(count, 1))} onClose={() => setOpen(false)} /> : <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Reopen find bar</Button>}
      <Cap>The query drives OutputArea's <code>searchQuery</code>; it reports matches back through <code>onSearchMatchCount</code> (highlight-text in the DOM, the iframe search for isolated outputs).</Cap>
      <OutputArea outputs={[S.RESULT_PLAIN, { output_type: 'stream', name: 'stdout', text: 'Grouped by region: 4 groups\nTop region: NA\n' }]} searchQuery={open ? q : ''} onSearchMatchCount={setCount} />
    </div>
  )
}

/* ── Editor ────────────────────────────────────────────────────── */
function Editor({ lang, initial, readOnly }: { lang: SupportedLanguage; initial: string; readOnly?: boolean }) {
  const dark = useDarkMode()
  const [v, setV] = useState(initial)
  return (
    <Tile label={<>{languageDisplayNames[lang] ?? lang}<span className="gx-mime">{readOnly ? 'readOnly' : `${v.split('\n').length} lines`}</span></>} wide>
      <div className="gx-editor"><CodeMirrorEditor value={v} language={lang} onValueChange={setV} theme={dark ? 'dark' : 'light'} readOnly={readOnly} lineWrapping /></div>
    </Tile>
  )
}
function EditorExample() {
  const langs = Object.keys(S.CODE) as (keyof typeof S.CODE)[]
  const magic = detectCellMagic(S.CODE.ipython)
  return (
    <div className="gx-stack">
      <Grid min={340}>{langs.map(l => <Editor key={l} lang={l} initial={S.CODE[l]} />)}</Grid>
      <Cap>detectCellMagic on the IPython cell → <code>{JSON.stringify(magic)}</code>. A read-only editor with a placeholder:</Cap>
      <Editor lang="python" initial="" readOnly />
    </div>
  )
}

/* ── Widgets ───────────────────────────────────────────────────── */
function StoreExample() {
  const models = useWidgetModels()
  const { store } = useWidgetStoreRequired()
  const log = useSyncExternalStore(cb => KernelCtx.current?.subscribe(cb) ?? (() => {}), () => KernelCtx.current?.log.length ? KernelCtx.current.log[0].at + KernelCtx.current.log.length : 0)
  const entries = KernelCtx.current?.log ?? []
  const byModule = [...models.values()].reduce<Record<string, number>>((a, m) => { a[m.modelModule || '?'] = (a[m.modelModule || '?'] ?? 0) + 1; return a }, {})
  return (
    <div className="gx-stack" data-log={log}>
      <Row>
        <Badge>{models.size} models in the store</Badge>
        {Object.entries(byModule).map(([m, n]) => <Badge key={m} variant="outline">{m} · {n}</Badge>)}
        <Button size="sm" variant="outline" onClick={() => store.updateModel('int_progress', { value: Math.round(Math.random() * 10) })}>store.updateModel(int_progress)</Button>
      </Row>
      <Cap>Comm traffic between the widgets and the pretend kernel (newest first). Drag a slider, click a button, draw on the canvas:</Cap>
      <div className="gx-kernel-log">
        {entries.length === 0 && <span className="gx-muted">no messages yet</span>}
        {entries.map((e, i) => <div key={`${e.at}-${i}`}><span className={`gx-dir gx-dir--${e.dir}`}>{e.dir === 'in' ? 'frontend → kernel' : 'kernel → frontend'}</span><code>{e.type}</code><code>{e.commId}</code><span className="gx-muted">{e.summary}</span></div>)}
      </div>
    </div>
  )
}
function WidgetViewExample() {
  return (
    <Grid min={250}>
      <W id="int_slider" note="built-in control" />
      <W id="anywidget" note="anywidget → AnyWidgetView" />
      <W id="mystery" note="no registered component" />
      <Tile label={<><code>model not opened yet</code><span>loading state</span></>}><WidgetView modelId="never-opened" /></Tile>
    </Grid>
  )
}
const AnyWidgetExample = () => (
  <div className="gx-stack">
    <Cap>A real anywidget: inline <code>_esm</code> + <code>_css</code>, loaded via a blob URL and given the AFM model proxy (<code>model.get / set / save_changes / on</code>). The count is synced to the store; the number below is the same model rendered again.</Cap>
    <Row><W id="anywidget" /><W id="anywidget" note="same model, second view" /></Row>
  </div>
)
const Group = ({ ids, min = 260 }: { ids: (string | [string, string])[]; min?: number }) => (
  <Grid min={min}>{ids.map(x => (typeof x === 'string' ? <W key={x} id={x} /> : <W key={x[0]} id={x[0]} note={x[1]} />))}</Grid>
)
const SlidersExample = () => <Group ids={['int_slider', 'float_slider', 'float_log', 'int_range', 'float_range', 'sel_slider', 'sel_range', ['int_slider_v', 'orientation=vertical'], ['v_slider_dis', 'disabled'], ['v_slider_nor', 'readout=False'], ['v_range_dis', 'disabled'], ['v_sel_nor', 'readout=False']]} />
const ProgressExample = () => <Group ids={['int_progress', ['float_progress', 'bar_style=success'], ...[0, 1, 2, 3, 4].map(i => [`v_prog_${i}`, `bar_style=${['\'\'', 'success', 'info', 'warning', 'danger'][i]}`] as [string, string]), ['v_prog_vert', 'orientation=vertical']]} />
const NumericTextExample = () => <Group ids={['int_text', 'float_text', ['bint_text', 'min 1 · max 10'], ['bfloat_text', 'min 0 · max 1'], ['v_int_text_dis', 'disabled'], ['v_bfloat_dis', 'disabled']]} />
const BooleanExample = () => <Group ids={['checkbox', ['v_check_ind', 'indent=True'], ['v_check_dis', 'disabled'], ['toggle_btn', 'off'], ['v_toggle_on', 'on · success'], ['v_toggle_dis', 'disabled'], ['valid_ok', 'value=True'], ['valid_bad', 'value=False']]} />
const SelectionExample = () => <Group ids={['dropdown', ['v_dropdown_dis', 'disabled'], 'combobox', 'radio', ['v_radio_dis', 'disabled'], 'select', 'select_multi', 'toggle_btns', ['v_tbtns_style', 'button_style=warning']]} />
const TextExample = () => <Group ids={['text', ['v_text_empty', 'placeholder'], ['v_text_dis', 'disabled'], 'textarea', ['v_textarea_dis', 'disabled'], 'password', 'label', 'html_w', ['html_math', 'KaTeX']]} />
const MultiExample = () => <Group ids={['tags', 'colors', 'ints', 'floats']} />
const DateTimeExample = () => <Group ids={['color', ['v_color_concise', 'concise=True'], 'date', ['v_date_dis', 'disabled'], ['datetime', 'UTC'], ['naive_dt', 'naive local'], 'time_picker', ['time', 'TimeModel']]} />
const MediaWidgetsExample = () => (
  <div className="gx-stack">
    <Cap>Bytes arrive as comm buffers; <code>buffer_paths: [["value"]]</code> puts them on <code>state.value</code> and buffer-utils turns them into data URLs.</Cap>
    <Group ids={[['image', 'png bytes'], ['image_gif', 'gif bytes'], ['audio', 'mp3 bytes'], ['video', 'webm bytes']]} />
  </div>
)
const ActionsExample = () => (
  <div className="gx-stack">
    <Cap>The button's click is a custom comm message; the kernel answers by updating the Label and appending to the Output widget. The slider's value goes to the kernel, which recomputes the HTML. Play drives a progress bar through a jsdlink.</Cap>
    <Group ids={[['btn', 'click → kernel'], ['btn_label', 'updated by kernel'], ['kern_slider', 'value → kernel'], ['kern_html', 'computed by kernel'], ['play', 'press play'], ['play_progress', 'dlink from Play'], ['v_play_dis', 'disabled'], ['upload', 'multiple, .csv/.json'], ['v_upload_one', 'single image'], ['v_upload_dis', 'disabled']]} />
    <Cap>Every button_style, plus disabled:</Cap>
    <Group min={170} ids={[0, 1, 2, 3, 4, 5].map(i => `v_btn_${i}`).concat('v_btn_dis')} />
  </div>
)
const LayoutExample = () => (
  <div className="gx-stack">
    <Group min={330} ids={['box', 'hbox', ['vbox', 'box_style=info'], ['gridbox', 'LayoutModel grid']]} />
    <Group min={330} ids={['accordion', 'tab']} />
    <Group min={330} ids={[['stack_pick', 'jsdlink → Stack.selected_index'], ['stack', 'StackModel']]} />
  </div>
)
const LinksExample = () => (
  <div className="gx-stack">
    <Cap>LinkModel (jslink, two-way) and DirectionalLinkModel (jsdlink, one-way) render nothing; they are store-level subscriptions. Their views are mounted here, and the widgets show the effect.</Cap>
    <Group ids={[['link_a', 'link source'], ['link_b', 'two-way target'], ['dlink_src', 'dlink source'], ['dlink_dst', 'one-way target']]} />
    <Group ids={[['link', 'headless'], ['dlink', 'headless'], ['stack_link', 'headless'], ['play_link', 'headless']]} />
  </div>
)
const ControllerExample = () => (
  <div className="gx-stack">
    <Cap>ControllerModel polls the Gamepad API (plug in a gamepad and press a button to connect). Its ControllerButton and ControllerAxis children are shown on their own with kernel-provided values.</Cap>
    <Group ids={['pad', ['pad_b0', 'pressed'], ['pad_b1', 'half pressed'], ['pad_b2', 'released'], ['pad_a0', 'axis −0.45'], ['pad_a1', 'axis 0.70']]} />
  </div>
)
const OutputWidgetExample = () => (
  <div className="gx-stack">
    <Cap>OutputModel accumulates outputs sent as custom messages. Click the button — each click is captured by <code>with out:</code> in the kernel.</Cap>
    <Row><W id="btn" /><W id="out" wide /></Row>
  </div>
)
const CanvasExample = () => (
  <div className="gx-stack">
    <Cap>The kernel answers the canvas's <code>client_ready</code> with a binary batch of drawing commands sent to the CanvasManagerModel, which routes them by <code>switchCanvas</code>. Click the canvas: mouse_down goes to the kernel, which draws a point.</Cap>
    <Row><W id="canvas" wide /><W id="canvas_mgr" note="headless router" /></Row>
  </div>
)

/* ── Isolation ─────────────────────────────────────────────────── */
const FRAME_HTML = `<div style="font:14px system-ui;padding:6px 4px">
<h3 style="margin:0 0 6px">Rendered inside an IsolatedFrame</h3>
<p style="margin:0 0 10px;opacity:.75">A sandboxed blob-URL iframe: scripts run, but cannot reach the parent page.</p>
<button id="b" style="font:inherit;padding:6px 12px;border-radius:8px;border:1px solid #5250e9;background:#5250e9;color:#fff;cursor:pointer">clicked 0×</button>
<script>let n=0;document.getElementById('b').onclick=e=>{e.target.textContent='clicked '+(++n)+'×'}</script></div>`
function IsolatedFrameExample() {
  const dark = useDarkMode()
  const ref = useRef<IsolatedFrameHandle>(null)
  const [h, setH] = useState(0)
  return (
    <div className="gx-stack">
      <Row>
        <Button size="sm" variant="outline" onClick={() => ref.current?.render({ mimeType: 'text/html', data: FRAME_HTML })}>render(text/html)</Button>
        <Button size="sm" variant="outline" onClick={() => ref.current?.render({ mimeType: 'text/markdown', data: S.MARKDOWN })}>render(text/markdown)</Button>
        <Button size="sm" variant="outline" onClick={() => ref.current?.render({ mimeType: 'image/svg+xml', data: S.SVG_CHART })}>render(image/svg+xml)</Button>
        <Button size="sm" variant="ghost" onClick={() => ref.current?.clear()}>clear()</Button>
        <span className="gx-muted">height {h}px</span>
      </Row>
      <div className="gx-frame"><IsolatedFrame ref={ref} id="gallery-frame" initialContent={{ mimeType: 'text/html', data: FRAME_HTML }} darkMode={dark} minHeight={80} maxHeight={480} onResize={setH} /></div>
    </div>
  )
}
const IsolationTestExample = () => <div className="gx-isotest"><IsolationTest /></div>
const RendererExample = () => (
  <div className="gx-stack">
    <Cap>The isolated-renderer bundle (built by <code>npm run build:renderer</code>, inlined by NteractProvider) is what runs inside every frame on this page. Here it renders markdown with KaTeX and a live widget whose state is bridged to the parent store over postMessage:</Cap>
    <OutputArea outputs={[S.DISPLAY_MD, { output_type: 'display_data', data: { 'application/vnd.jupyter.widget-view+json': { model_id: 'tab', version_major: 2, version_minor: 0 } } }]} />
  </div>
)

/* ── Lib ───────────────────────────────────────────────────────── */
function Crashy({ crash }: { crash: boolean }): ReactNode {
  if (crash) throw new Error('Cannot read properties of undefined (reading "mrr")')
  return <div className="gx-ok">Child rendered fine. Click “Throw” to crash it.</div>
}
function ErrorBoundaryExample() {
  const [crash, setCrash] = useState(false)
  return (
    <div className="gx-stack">
      <Row><Button size="sm" variant="destructive" onClick={() => setCrash(true)}>Throw</Button><span className="gx-muted">ErrorBoundary with resetKeys + OutputErrorFallback</span></Row>
      <ErrorBoundary resetKeys={[crash]} fallback={(err, reset) => <OutputErrorFallback error={err} outputIndex={0} onRetry={() => { setCrash(false); reset() }} />}>
        <Crashy crash={crash} />
      </ErrorBoundary>
      <Cap>The two fallbacks on their own:</Cap>
      <OutputErrorFallback error={new Error('Unexpected token < in JSON at position 0')} outputIndex={2} onRetry={() => {}} />
      <WidgetErrorFallback error={new Error('model.get is not a function')} modelId="a1b2c3" modelName="IntSliderModel" onRetry={() => {}} />
    </div>
  )
}
function HighlightExample() {
  const [q, setQ] = useState('revenue')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => (ref.current ? highlightTextInDom(ref.current, q) : undefined), [q])
  return (
    <div className="gx-stack">
      <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Search…" className="gx-input" />
      <div ref={ref} className="gx-prose">Quarterly revenue grew 12% while churn stayed flat. Revenue by region is led by NA; EMEA revenue grew fastest. Refresh the revenue extract nightly.</div>
    </div>
  )
}
function DarkModeExample() {
  const dark = useDarkMode()
  return <Row><Badge variant={dark ? 'default' : 'outline'}>useDarkMode() → {String(dark)}</Badge><span className="gx-muted">reads data-theme on &lt;html&gt;; toggle the theme in the top bar</span></Row>
}

/* ── Icons + selection card ────────────────────────────────────── */
const ICONS = [['PythonIcon', PythonIcon], ['DenoIcon', DenoIcon], ['UvIcon', UvIcon], ['CondaIcon', CondaIcon], ['PixiIcon', PixiIcon]] as const
const IconsExample = () => <Row>{ICONS.map(([n, I]) => <Tile key={n} label={n}><div className="gx-icons"><I className="gx-icon-sm" /><I className="gx-icon-lg" /></div></Tile>)}</Row>
function SelectionCardExample() {
  const opts = [
    { k: 'python', icon: PythonIcon, title: 'Python', subtitle: 'ipykernel', description: 'pandas, numpy and the scientific stack.' },
    { k: 'deno', icon: DenoIcon, title: 'Deno', subtitle: 'TypeScript', description: 'A secure TypeScript runtime with Jupyter support.' },
    { k: 'uv', icon: UvIcon, title: 'uv', subtitle: 'packages', description: 'Fast, reproducible Python environments.' },
    { k: 'conda', icon: CondaIcon, title: 'Conda', subtitle: 'packages', description: 'Binary packages across languages.' },
    { k: 'pixi', icon: PixiIcon, title: 'Pixi', subtitle: 'packages', description: 'Conda packages with a lockfile.' },
  ] as const
  const [sel, setSel] = useState<string>('python')
  const i = opts.findIndex(o => o.k === sel)
  return (
    <div className="gx-stack">
      <Grid min={220}>{opts.map(o => <SelectionCard key={o.k} selected={sel === o.k} onClick={() => setSel(o.k)} icon={o.icon} title={o.title} subtitle={o.subtitle} description={o.description} colorClass={BRAND_COLORS[o.k]} />)}</Grid>
      <Row><PageDots current={i} total={opts.length} /><span className="gx-muted">PageDots — step {i + 1} of {opts.length}</span></Row>
    </div>
  )
}

/* ── shadcn primitives ─────────────────────────────────────────── */
const ui = (name: string, title: string, desc: string, imports: string, Example: ComponentType, registry: RegistryItem[] = []): Section => ({ id: `ui-${name}`, title, category: 'UI primitives', desc, imports, registry, files: [`ui/${name}.tsx`], Example })
const noFocus = (e: Event) => e.preventDefault()
const noop = () => {}

const BTN_VARIANTS = ['default', 'secondary', 'outline', 'ghost', 'destructive', 'link'] as const
const BTN_SIZES = ['xs', 'sm', 'default', 'lg'] as const
const ICON_SIZES = ['icon-xs', 'icon-sm', 'icon', 'icon-lg'] as const
function ButtonDemo() {
  return (
    <div className="gx-matrix" style={{ ['--cols' as string]: 9 }}>
      <span />{BTN_SIZES.map(z => <b key={z}>{z}</b>)}{ICON_SIZES.map(z => <b key={z}>{z}</b>)}<b>disabled</b>
      {BTN_VARIANTS.map(v => (
        <div key={v} className="gx-matrix__row">
          <span>{v}</span>
          {BTN_SIZES.map(z => <div key={z}><Button variant={v} size={z}>{z === 'xs' || z === 'sm' ? <Sparkles /> : null}Run</Button></div>)}
          {ICON_SIZES.map(z => <div key={z}><Button variant={v} size={z} aria-label={`${v} ${z}`}><Sparkles /></Button></div>)}
          <div><Button variant={v} disabled>Run</Button></div>
        </div>
      ))}
    </div>
  )
}
const BADGE_VARIANTS = ['default', 'secondary', 'destructive', 'outline', 'ghost', 'link'] as const
const BadgeDemo = () => (
  <div className="gx-stack">
    <Row>{BADGE_VARIANTS.map(v => <Badge key={v} variant={v}>{v}</Badge>)}</Row>
    <Row>{BADGE_VARIANTS.map(v => <Badge key={v} variant={v}><Sparkles />with icon</Badge>)}</Row>
    <Row>{BADGE_VARIANTS.map(v => <Badge key={v} variant={v} asChild><a href="#/nteract/ui-badge">as link</a></Badge>)}</Row>
  </div>
)
function ToggleDemo() {
  return (
    <div className="gx-matrix" style={{ ['--cols' as string]: 4 }}>
      <span /><b>off</b><b>on</b><b>disabled</b><b>disabled on</b>
      {(['default', 'outline'] as const).flatMap(v => (['sm', 'default', 'lg'] as const).map(z => (
        <div key={v + z} className="gx-matrix__row">
          <span>{v} · {z}</span>
          <div><Toggle variant={v} size={z} aria-label="bold"><Bold /></Toggle></div>
          <div><Toggle variant={v} size={z} defaultPressed aria-label="italic"><Italic /> Italic</Toggle></div>
          <div><Toggle variant={v} size={z} disabled aria-label="underline"><Underline /></Toggle></div>
          <div><Toggle variant={v} size={z} disabled defaultPressed aria-label="underline on"><Underline /></Toggle></div>
        </div>
      )))}
    </div>
  )
}
const ToggleGroupDemo = () => (
  <div className="gx-stack">
    <Row><ToggleGroup type="single" defaultValue="bar" variant="outline"><ToggleGroupItem value="line">Line</ToggleGroupItem><ToggleGroupItem value="bar">Bar</ToggleGroupItem><ToggleGroupItem value="area">Area</ToggleGroupItem></ToggleGroup><span className="gx-muted">single · outline</span></Row>
    <Row><ToggleGroup type="multiple" defaultValue={['b', 'u']}><ToggleGroupItem value="b" aria-label="bold"><Bold /></ToggleGroupItem><ToggleGroupItem value="i" aria-label="italic"><Italic /></ToggleGroupItem><ToggleGroupItem value="u" aria-label="underline"><Underline /></ToggleGroupItem></ToggleGroup><span className="gx-muted">multiple · default</span></Row>
    <Row><ToggleGroup type="single" defaultValue="m" size="sm" variant="outline"><ToggleGroupItem value="s">S</ToggleGroupItem><ToggleGroupItem value="m">M</ToggleGroupItem><ToggleGroupItem value="l" disabled>L (disabled item)</ToggleGroupItem></ToggleGroup><span className="gx-muted">size sm, one disabled item</span></Row>
    <Row><ToggleGroup type="single" defaultValue="a" disabled size="lg"><ToggleGroupItem value="a">All</ToggleGroupItem><ToggleGroupItem value="n">None</ToggleGroupItem></ToggleGroup><span className="gx-muted">size lg, whole group disabled</span></Row>
  </div>
)
const CheckboxDemo = () => (
  <Row>
    <Label className="gx-label"><Checkbox /> unchecked</Label>
    <Label className="gx-label"><Checkbox defaultChecked /> checked</Label>
    <Label className="gx-label"><Checkbox checked="indeterminate" /> indeterminate</Label>
    <Label className="gx-label"><Checkbox disabled /> disabled</Label>
    <Label className="gx-label"><Checkbox disabled defaultChecked /> disabled checked</Label>
    <Label className="gx-label"><Checkbox aria-invalid /> invalid</Label>
  </Row>
)
const RadioDemo = () => (
  <Row>
    <RadioGroup defaultValue="weekly" className="gx-stack gx-stack--tight">
      {['daily', 'weekly', 'monthly'].map(v => <Label key={v} className="gx-label"><RadioGroupItem value={v} /> {v}</Label>)}
      <Label className="gx-label"><RadioGroupItem value="hourly" disabled /> hourly (disabled item)</Label>
    </RadioGroup>
    <RadioGroup defaultValue="a" disabled className="gx-stack gx-stack--tight">
      <Label className="gx-label"><RadioGroupItem value="a" /> whole group disabled, selected</Label>
      <Label className="gx-label"><RadioGroupItem value="b" /> whole group disabled</Label>
    </RadioGroup>
  </Row>
)
function SliderDemo() {
  const [v, setV] = useState([40])
  const [r, setR] = useState([20, 70])
  return (
    <div className="gx-stack">
      <Row><Slider value={v} onValueChange={setV} max={100} className="gx-w240" /><span className="gx-muted">single · {v[0]}</span></Row>
      <Row><Slider value={r} onValueChange={setR} max={100} className="gx-w240" /><span className="gx-muted">range · {r.join(' – ')}</span></Row>
      <Row><Slider defaultValue={[30]} max={100} step={10} className="gx-w240" /><span className="gx-muted">step=10</span></Row>
      <Row><Slider defaultValue={[60]} max={100} disabled className="gx-w240" /><span className="gx-muted">disabled</span></Row>
      <Row><div className="gx-vslider"><Slider defaultValue={[50]} max={100} orientation="vertical" /></div><span className="gx-muted">vertical</span></Row>
    </div>
  )
}
function ProgressDemo() {
  const [v, setV] = useState(35)
  useEffect(() => { const t = setInterval(() => setV(x => (x >= 100 ? 0 : x + 5)), 600); return () => clearInterval(t) }, [])
  return <div className="gx-stack">{[0, 25, 72, 100].map(n => <Row key={n} wrap={false}><Progress value={n} /><span className="gx-muted gx-w60">{n}%</span></Row>)}<Row wrap={false}><Progress value={v} /><span className="gx-muted gx-w60">live {v}%</span></Row></div>
}
const TabsDemo = () => (
  <div className="gx-stack">
    <Tabs defaultValue="out"><TabsList><TabsTrigger value="out">Output</TabsTrigger><TabsTrigger value="logs">Logs</TabsTrigger><TabsTrigger value="vars">Variables</TabsTrigger><TabsTrigger value="dis" disabled>Disabled</TabsTrigger></TabsList><TabsContent value="out">4 rows returned.</TabsContent><TabsContent value="logs">No warnings.</TabsContent><TabsContent value="vars">df, accounts, churn</TabsContent></Tabs>
    <Tabs defaultValue="b"><TabsList variant="line"><TabsTrigger value="a">Line variant</TabsTrigger><TabsTrigger value="b">Selected</TabsTrigger><TabsTrigger value="c" disabled>Disabled</TabsTrigger></TabsList><TabsContent value="a">Underlined tabs.</TabsContent><TabsContent value="b">Second panel is selected.</TabsContent></Tabs>
    <Tabs defaultValue="v" orientation="vertical" className="gx-vtabs"><TabsList><TabsTrigger value="v">Vertical</TabsTrigger><TabsTrigger value="w">Second</TabsTrigger></TabsList><TabsContent value="v">orientation=vertical</TabsContent><TabsContent value="w">Second panel</TabsContent></Tabs>
  </div>
)
const AccordionDemo = () => (
  <div className="gx-two">
    <Tile label="type=single · first open">
      <Accordion type="single" collapsible defaultValue="a" className="gx-w-full">
        <AccordionItem value="a"><AccordionTrigger>What does refresh do?</AccordionTrigger><AccordionContent>It replays the saved steps against fresh data.</AccordionContent></AccordionItem>
        <AccordionItem value="b"><AccordionTrigger>Who can see this notebook?</AccordionTrigger><AccordionContent>Everyone on the growth team.</AccordionContent></AccordionItem>
        <AccordionItem value="c" disabled><AccordionTrigger>Disabled item</AccordionTrigger><AccordionContent>Unreachable.</AccordionContent></AccordionItem>
      </Accordion>
    </Tile>
    <Tile label="type=multiple · two open">
      <Accordion type="multiple" defaultValue={['x', 'y']} className="gx-w-full">
        <AccordionItem value="x"><AccordionTrigger>Schema</AccordionTrigger><AccordionContent>id · email · mrr</AccordionContent></AccordionItem>
        <AccordionItem value="y"><AccordionTrigger>Lineage</AccordionTrigger><AccordionContent>invoices → accounts</AccordionContent></AccordionItem>
        <AccordionItem value="z"><AccordionTrigger>Closed item</AccordionTrigger><AccordionContent>Owners</AccordionContent></AccordionItem>
      </Accordion>
    </Tile>
  </div>
)
const CollapsibleDemo = () => (
  <Row>
    {([['open', true, false], ['closed', false, false], ['disabled', false, true]] as const).map(([l, open, dis]) => (
      <Tile key={l} label={l}>
        <Collapsible defaultOpen={open} disabled={dis} className="gx-stack gx-stack--tight">
          <CollapsibleTrigger asChild><Button variant="outline" size="sm" disabled={dis}>3 hidden columns <ChevronDown /></Button></CollapsibleTrigger>
          <CollapsibleContent className="gx-stack gx-stack--tight"><code>signup_source</code><code>utm_campaign</code><code>legacy_id</code></CollapsibleContent>
        </Collapsible>
      </Tile>
    ))}
  </Row>
)
function DialogDemo() {
  const [frame, setFrame] = useState<HTMLDivElement | null>(null)
  return (
    <div className="gx-stack">
      <Row>
        <Dialog>
          <DialogTrigger asChild><Button variant="outline">Open the real dialog…</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Rename notebook</DialogTitle><DialogDescription>The new name is used in links and the sidebar.</DialogDescription></DialogHeader>
            <Input defaultValue="Churn review" />
            <DialogFooter><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><DialogClose asChild><Button>Save</Button></DialogClose></DialogFooter>
          </DialogContent>
        </Dialog>
        <span className="gx-muted">Below: the same parts held open in a static frame (portalled into the frame instead of the page).</span>
      </Row>
      <div className="gx-overlay-frame" ref={setFrame}>
        <div className="gx-overlay-frame__backdrop" />
        {frame && (
          <Dialog open modal={false}>
            <DialogPortal container={frame}>
              <DialogPrimitive.Content className="gx-static-dialog" onOpenAutoFocus={noFocus} onInteractOutside={e => e.preventDefault()}>
                <DialogHeader><DialogTitle>Delete 3 cells?</DialogTitle><DialogDescription>Their outputs are deleted too. This cannot be undone.</DialogDescription></DialogHeader>
                <DialogFooter><Button variant="outline">Cancel</Button><Button variant="destructive">Delete</Button></DialogFooter>
              </DialogPrimitive.Content>
            </DialogPortal>
          </Dialog>
        )}
      </div>
    </div>
  )
}
function DropdownBody() {
  const [wrap, setWrap] = useState(true)
  const [kernel, setKernel] = useState('python3')
  return (
    <>
      <DropdownMenuLabel>Notebook</DropdownMenuLabel>
      <DropdownMenuItem>Run all <DropdownMenuShortcut>⇧↵</DropdownMenuShortcut></DropdownMenuItem>
      <DropdownMenuItem>Restart kernel</DropdownMenuItem>
      <DropdownMenuItem disabled>Export (disabled)</DropdownMenuItem>
      <DropdownMenuItem variant="destructive">Delete notebook</DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuCheckboxItem checked={wrap} onCheckedChange={v => setWrap(!!v)}>Wrap lines</DropdownMenuCheckboxItem>
      <DropdownMenuSeparator />
      <DropdownMenuLabel>Kernel</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={kernel} onValueChange={setKernel}>
        <DropdownMenuRadioItem value="python3">Python 3</DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="deno">Deno</DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
    </>
  )
}
const DropdownDemo = () => (
  <Row>
    <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline">Interactive menu <ChevronDown /></Button></DropdownMenuTrigger><DropdownMenuContent className="gx-w220"><DropdownBody /></DropdownMenuContent></DropdownMenu>
    <div className="gx-open-slot gx-open-slot--tall">
      <DropdownMenu open modal={false} onOpenChange={noop}><DropdownMenuTrigger asChild><Button variant="secondary">Forced open <ChevronDown /></Button></DropdownMenuTrigger><DropdownMenuContent className="gx-w220" align="start" onCloseAutoFocus={noFocus}><DropdownBody /></DropdownMenuContent></DropdownMenu>
    </div>
  </Row>
)
const SelectBody = () => (
  <SelectContent>
    <SelectGroup><SelectLabel>Engines</SelectLabel><SelectItem value="pandas">pandas</SelectItem><SelectItem value="polars">polars</SelectItem><SelectItem value="duckdb">DuckDB</SelectItem><SelectItem value="spark" disabled>Spark (disabled)</SelectItem></SelectGroup>
  </SelectContent>
)
function SelectDemo() {
  const [v, setV] = useState('duckdb')
  return (
    <div className="gx-stack">
      <Row>
        <Select value={v} onValueChange={setV}><SelectTrigger className="gx-w200"><SelectValue placeholder="Engine" /></SelectTrigger><SelectBody /></Select>
        <span className="gx-muted">value: {v}</span>
        <Select><SelectTrigger className="gx-w200"><SelectValue placeholder="Placeholder (no value)" /></SelectTrigger><SelectBody /></Select>
        <Select defaultValue="pandas"><SelectTrigger size="sm" className="gx-w160"><SelectValue /></SelectTrigger><SelectBody /></Select>
        <Select disabled defaultValue="polars"><SelectTrigger className="gx-w160"><SelectValue /></SelectTrigger><SelectBody /></Select>
        <Select><SelectTrigger aria-invalid className="gx-w160"><SelectValue placeholder="Invalid" /></SelectTrigger><SelectBody /></Select>
      </Row>
      <span className="gx-muted">Opening a Select locks page scroll (it is modal), so its open state is captured in the browser check rather than forced open here.</span>
    </div>
  )
}
const PopoverBody = () => (
  <>
    <PopoverHeader><PopoverTitle>Refresh schedule</PopoverTitle><PopoverDescription>Runs with no AI, on saved steps.</PopoverDescription></PopoverHeader>
    <div className="gx-stack gx-stack--tight gx-mt"><Label htmlFor="gx-cron">Every</Label><Input id="gx-cron" defaultValue="weekday 08:45" /></div>
  </>
)
const PopoverDemo = () => (
  <Row>
    <Popover><PopoverTrigger asChild><Button variant="outline"><Calendar /> Interactive…</Button></PopoverTrigger><PopoverContent><PopoverBody /></PopoverContent></Popover>
    <div className="gx-open-slot">
      <Popover open onOpenChange={noop}><PopoverTrigger asChild><Button variant="secondary"><Calendar /> Forced open</Button></PopoverTrigger><PopoverContent align="start" onOpenAutoFocus={noFocus} onInteractOutside={e => e.preventDefault()}><PopoverBody /></PopoverContent></Popover>
    </div>
  </Row>
)
const HoverBody = () => <div className="gx-stack gx-stack--tight"><b>accounts · v7</b><span className="gx-muted">12,408 rows, refreshed 08:45 UTC</span></div>
const HoverCardDemo = () => (
  <Row>
    <HoverCard openDelay={100}><HoverCardTrigger asChild><Button variant="link">@accounts (hover me)</Button></HoverCardTrigger><HoverCardContent><HoverBody /></HoverCardContent></HoverCard>
    <div className="gx-open-slot gx-open-slot--short">
      <HoverCard open onOpenChange={noop}><HoverCardTrigger asChild><Button variant="link">@invoices (forced open)</Button></HoverCardTrigger><HoverCardContent align="start"><HoverBody /></HoverCardContent></HoverCard>
    </div>
  </Row>
)
function CommandDemo() {
  const [picked, setPicked] = useState('')
  const [open, setOpen] = useState(false)
  const items = (
    <CommandList>
      <CommandEmpty>No results.</CommandEmpty>
      <CommandGroup heading="Cells">
        <CommandItem onSelect={() => setPicked('Insert code cell')}><Code2 />Insert code cell<CommandShortcut>B</CommandShortcut></CommandItem>
        <CommandItem onSelect={() => setPicked('Insert markdown cell')}><BookOpen />Insert markdown cell<CommandShortcut>M</CommandShortcut></CommandItem>
        <CommandItem disabled><Sparkles />Ask AI (disabled)</CommandItem>
      </CommandGroup>
      <CommandSeparator />
      <CommandGroup heading="Notebook">
        <CommandItem onSelect={() => setPicked('Share')}><Users />Share</CommandItem>
        <CommandItem onSelect={() => setPicked('Settings')}><Settings />Settings<CommandShortcut>⌘,</CommandShortcut></CommandItem>
      </CommandGroup>
    </CommandList>
  )
  return (
    <div className="gx-stack">
      <div className="gx-two">
        <Tile label="inline palette (open)"><Command className="gx-command"><CommandInput placeholder="Type a command…" />{items}</Command></Tile>
        <Tile label="filtered: no results"><Command className="gx-command"><CommandInput value="zzz" placeholder="…" /><CommandList><CommandEmpty>No results for “zzz”.</CommandEmpty><CommandGroup heading="Cells"><CommandItem>Insert code cell</CommandItem></CommandGroup></CommandList></Command></Tile>
      </div>
      <Row><Button variant="outline" size="sm" onClick={() => setOpen(true)}>Open CommandDialog</Button>{picked && <span className="gx-log">selected: {picked}</span>}</Row>
      <CommandDialog open={open} onOpenChange={setOpen}><CommandInput placeholder="Search commands…" />{items}</CommandDialog>
    </div>
  )
}
const InputDemo = () => (
  <Row>
    <Input placeholder="placeholder" className="gx-w200" />
    <Input defaultValue="with a value" className="gx-w200" />
    <Input type="number" defaultValue={42} className="gx-w120" />
    <Input type="password" defaultValue="hunter2" className="gx-w160" />
    <Input type="file" className="gx-w240" />
    <Input disabled placeholder="disabled" className="gx-w160" />
    <Input aria-invalid defaultValue="invalid" className="gx-w160" />
  </Row>
)
const TextareaDemo = () => (
  <div className="gx-two">
    <Textarea defaultValue={'Notes for Monday:\n- check APAC pricing'} rows={3} />
    <Textarea placeholder="placeholder" rows={3} />
    <Textarea disabled defaultValue="disabled" rows={2} />
    <Textarea aria-invalid defaultValue="invalid" rows={2} />
  </div>
)
const AvatarDemo = () => (
  <div className="gx-matrix" style={{ ['--cols' as string]: 3 }}>
    <span /><b>image</b><b>fallback</b><b>with badge</b>
    {(['sm', 'default', 'lg'] as const).map(z => (
      <div key={z} className="gx-matrix__row">
        <span>size={z}</span>
        <div><Avatar size={z}><AvatarImage src={S.avatarSrc('AL', '#5250e9')} alt="Ada" /><AvatarFallback>AL</AvatarFallback></Avatar></div>
        <div><Avatar size={z}><AvatarFallback>GH</AvatarFallback></Avatar></div>
        <div><Avatar size={z}><AvatarImage src={S.avatarSrc('KJ', '#d97706')} alt="Kat" /><AvatarFallback>KJ</AvatarFallback><AvatarBadge /></Avatar></div>
      </div>
    ))}
    <div className="gx-matrix__row"><span>group</span><div><AvatarGroup>{S.USERS.slice(0, 3).map(u => <Avatar key={u.id}><AvatarImage src={S.avatarSrc(u.name[0], u.color)} alt={u.name} /><AvatarFallback>{u.name[0]}</AvatarFallback></Avatar>)}<AvatarGroupCount>+5</AvatarGroupCount></AvatarGroup></div><div /><div /></div>
  </div>
)

const imp = (names: string, path: string) => `import { ${names} } from "@/components/${path}"`

/* ── the section list (the single source of truth for coverage) ── */
export const SECTIONS: Section[] = [
  // Outputs
  { id: 'ansi-output', title: 'ANSI Output', category: 'Outputs', desc: 'Renders ANSI escape sequences (colours, bold, backgrounds) from streams and tracebacks.', imports: imp('AnsiOutput, AnsiStreamOutput, AnsiErrorOutput', 'outputs/ansi-output'), registry: ['ansi-output'], files: ['outputs/ansi-output.tsx'], Example: AnsiExample },
  { id: 'image-output', title: 'Image Output', category: 'Outputs', desc: 'Base64 image data from Jupyter outputs — PNG, JPEG, animated GIF and WebP.', imports: imp('ImageOutput', 'outputs/image-output'), registry: ['image-output'], files: ['outputs/image-output.tsx'], Example: ImageExample },
  { id: 'html-output', title: 'HTML Output', category: 'Outputs', desc: 'Rich HTML such as a pandas DataFrame. Trusted content only — untrusted HTML belongs in an IsolatedFrame.', imports: imp('HtmlOutput', 'outputs/html-output'), registry: ['html-output'], files: ['outputs/html-output.tsx'], Example: HtmlExample },
  { id: 'svg-output', title: 'SVG Output', category: 'Outputs', desc: 'SVG graphics such as matplotlib figures and diagrams.', imports: imp('SvgOutput', 'outputs/svg-output'), registry: ['svg-output'], files: ['outputs/svg-output.tsx'], Example: SvgExample },
  { id: 'markdown-output', title: 'Markdown Output', category: 'Outputs', desc: 'GitHub-flavoured markdown with tables, task lists, KaTeX math and highlighted, copyable code.', imports: imp('MarkdownOutput', 'outputs/markdown-output'), registry: ['markdown-output'], files: ['outputs/markdown-output.tsx'], Example: MarkdownExample },
  { id: 'json-output', title: 'JSON Output', category: 'Outputs', desc: 'An expandable tree for application/json results.', imports: imp('JsonOutput', 'outputs/json-output'), registry: ['json-output'], files: ['outputs/json-output.tsx'], Example: JsonExample },
  { id: 'media-provider', title: 'Media Provider', category: 'Outputs', desc: 'Context that shares custom renderers and MIME priority with every MediaRouter below it.', imports: imp('MediaProvider', 'outputs/media-provider'), registry: ['media-provider'], files: ['outputs/media-provider.tsx'], Example: MediaProviderExample },
  { id: 'media-router', title: 'Media Router', category: 'Outputs', desc: 'Picks the richest MIME type in a bundle and dispatches it to the right renderer — one tile per MIME type.', imports: imp('MediaRouter, DEFAULT_PRIORITY, getSelectedMimeType', 'outputs/media-router'), registry: ['media-router'], files: ['outputs/media-router.tsx'], Example: MediaRouterExample },
  { id: 'output-area', title: 'OutputArea', category: 'Outputs', desc: 'Renders a cell’s outputs — stream, error, execute_result, display_data — collapsing, scrolling and isolating as needed.', imports: imp('OutputArea, type JupyterOutput', 'cell/OutputArea'), registry: ['output-area'], files: ['cell/OutputArea.tsx'], Example: OutputAreaExample },
  // Cells
  { id: 'notebook', title: 'A working notebook', category: 'Cells', desc: 'Every cell primitive composed: container, header, type selector, count, status, presence, controls, editor, outputs and betweeners. Run, add, move, delete.', imports: imp('CellContainer, CellHeader, CellControls, PlayButton, CellBetweener', 'cell/…'), registry: [], files: [], Example: NotebookExample },
  { id: 'cell-container', title: 'CellContainer', category: 'Cells', desc: 'Focus and selection wrapper with the colour-coded gutter ribbon.', imports: imp('CellContainer', 'cell/CellContainer'), registry: ['cell-container'], files: ['cell/CellContainer.tsx', 'cell/gutter-colors.ts'], Example: CellContainerExample },
  { id: 'cell-betweener', title: 'CellBetweener', category: 'Cells', desc: 'The spacer between cells that keeps the gutter ribbon continuous; a natural home for “add cell” buttons.', imports: imp('CellBetweener', 'cell/CellBetweener'), registry: ['cell-betweener'], files: ['cell/CellBetweener.tsx'], Example: CellBetweenerExample },
  { id: 'cell-header', title: 'CellHeader', category: 'Cells', desc: 'Slot-based header with left and right content and an optional drag handle.', imports: imp('CellHeader', 'cell/CellHeader'), registry: ['cell-header'], files: ['cell/CellHeader.tsx'], Example: CellHeaderExample },
  { id: 'cell-controls', title: 'CellControls', category: 'Cells', desc: 'Source toggle, move controls and the ⋯ menu for clearing, deleting and moving cells.', imports: imp('CellControls', 'cell/CellControls'), registry: ['cell-controls'], files: ['cell/CellControls.tsx'], Example: CellControlsExample },
  { id: 'cell-type-button', title: 'CellTypeButton', category: 'Cells', desc: 'Colour-coded buttons per cell type: code, markdown, SQL, AI.', imports: imp('CellTypeButton, CodeCellButton, MarkdownCellButton, SqlCellButton, AiCellButton', 'cell/CellTypeButton'), registry: ['cell-type-button'], files: ['cell/CellTypeButton.tsx'], Example: CellTypeButtonExample },
  { id: 'cell-type-selector', title: 'CellTypeSelector', category: 'Cells', desc: 'Dropdown for changing a cell’s type, optionally limited to some types.', imports: imp('CellTypeSelector', 'cell/CellTypeSelector'), registry: ['cell-type-selector'], files: ['cell/CellTypeSelector.tsx'], Example: CellTypeSelectorExample },
  { id: 'execution-count', title: 'ExecutionCount', category: 'Cells', desc: 'The classic [n]: indicator, with [*] while running.', imports: imp('ExecutionCount', 'cell/ExecutionCount'), registry: ['execution-count'], files: ['cell/ExecutionCount.tsx'], Example: ExecutionCountExample },
  { id: 'execution-status', title: 'ExecutionStatus', category: 'Cells', desc: 'Badge for queued, running, completed and error states.', imports: imp('ExecutionStatus', 'cell/ExecutionStatus'), registry: ['execution-status'], files: ['cell/ExecutionStatus.tsx'], Example: ExecutionStatusExample },
  { id: 'play-button', title: 'PlayButton', category: 'Cells', desc: 'Run / stop / loading control driven by the execution state.', imports: imp('PlayButton', 'cell/PlayButton'), registry: ['play-button'], files: ['cell/PlayButton.tsx'], Example: PlayButtonExample },
  { id: 'runtime-health-indicator', title: 'RuntimeHealthIndicator', category: 'Cells', desc: 'Kernel connection status: idle, busy, connecting, disconnected.', imports: imp('RuntimeHealthIndicator', 'cell/RuntimeHealthIndicator'), registry: ['runtime-health-indicator'], files: ['cell/RuntimeHealthIndicator.tsx'], Example: RuntimeHealthExample },
  { id: 'collaborator-avatars', title: 'CollaboratorAvatars', category: 'Cells', desc: 'Who is in the notebook, with an overflow count.', imports: imp('CollaboratorAvatars', 'cell/CollaboratorAvatars'), registry: ['collaborator-avatars'], files: ['cell/CollaboratorAvatars.tsx'], Example: CollaboratorsExample },
  { id: 'presence-bookmarks', title: 'PresenceBookmarks', category: 'Cells', desc: 'Stacked avatars showing who is on a cell, with a hover card per user.', imports: imp('PresenceBookmarks', 'cell/PresenceBookmarks'), registry: ['presence-bookmarks'], files: ['cell/PresenceBookmarks.tsx'], Example: PresenceExample },
  { id: 'global-find-bar', title: 'GlobalFindBar', category: 'Cells', desc: 'Notebook-wide find with match count and Enter / Shift+Enter navigation, wired to OutputArea search.', imports: imp('GlobalFindBar', 'cell/GlobalFindBar'), registry: ['global-find-bar', 'highlight-text'], files: ['cell/GlobalFindBar.tsx'], Example: FindBarExample },
  // Editor
  { id: 'codemirror-editor', title: 'CodeMirror Editor', category: 'Editor', desc: 'CodeMirror 6 for cells: Python, IPython magics, SQL, Markdown, JSON, JavaScript, TypeScript, HTML and plain text, light and dark.', imports: imp('CodeMirrorEditor, detectCellMagic, languageDisplayNames', 'editor'), registry: ['codemirror-editor'], files: ['editor/codemirror-editor.tsx', 'editor/extensions.ts', 'editor/index.ts', 'editor/ipython.ts', 'editor/languages.ts', 'editor/search-highlight.ts', 'editor/themes.ts'], Example: EditorExample },
  // Widgets
  { id: 'widget-store', title: 'Widget Store', category: 'Widgets', desc: 'Pure React state for widget models. Every widget on this page was opened by comm_open messages fed through the comm router.', imports: imp('WidgetStoreProvider, useWidgetStore, useWidgetModel, useWidgetModelValue', 'widgets/widget-store-context'), registry: ['widget-store'], files: ['widgets/widget-store.ts', 'widgets/widget-store-context.tsx', 'widgets/use-comm-router.ts', 'widgets/buffer-utils.ts', 'widgets/link-subscriptions.ts', 'widgets/canvas-manager-subscriptions.ts', 'lib/uuid-polyfill.ts'], Example: StoreExample },
  { id: 'widget-view', title: 'Widget View', category: 'Widgets', desc: 'Routes a model id to its component: built-in control, anywidget, unsupported fallback or loading state.', imports: imp('WidgetView', 'widgets/widget-view'), registry: ['widget-view'], files: ['widgets/widget-view.tsx', 'widgets/widget-registry.ts'], Example: WidgetViewExample },
  { id: 'anywidget-view', title: 'AnyWidget View', category: 'Widgets', desc: 'Loads an anywidget ESM module and drives it with the AFM model interface.', imports: imp('AnyWidgetView', 'widgets/anywidget-view'), registry: ['anywidget-view'], files: ['widgets/anywidget-view.tsx'], Example: AnyWidgetExample },
  { id: 'w-sliders', title: 'Sliders', category: 'Widget controls', desc: 'IntSlider, FloatSlider, FloatLogSlider, the range sliders and the selection sliders.', imports: imp('IntSlider, FloatSlider, FloatLogSlider, IntRangeSlider, FloatRangeSlider, SelectionSliderWidget, SelectionRangeSliderWidget', 'widgets/controls'), registry: ['widget-controls'], files: ['widgets/controls/index.ts', 'widgets/use-layout-styles.ts', 'widgets/layout-utils.ts'], Example: SlidersExample },
  { id: 'w-progress', title: 'Progress', category: 'Widget controls', desc: 'IntProgress and FloatProgress with bar styles.', imports: imp('IntProgress, FloatProgress', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: ProgressExample },
  { id: 'w-numeric', title: 'Numeric text', category: 'Widget controls', desc: 'IntText, FloatText and their bounded variants.', imports: imp('IntTextWidget, FloatTextWidget, BoundedIntTextWidget, BoundedFloatTextWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: NumericTextExample },
  { id: 'w-boolean', title: 'Boolean', category: 'Widget controls', desc: 'Checkbox, ToggleButton and Valid.', imports: imp('CheckboxWidget, ToggleButtonWidget, ValidWidget', 'widgets/controls'), registry: ['widget-controls'], files: ['widgets/controls/button-style-utils.ts'], Example: BooleanExample },
  { id: 'w-selection', title: 'Selection', category: 'Widget controls', desc: 'Dropdown, Combobox, RadioButtons, Select, SelectMultiple and ToggleButtons.', imports: imp('DropdownWidget, ComboboxWidget, RadioButtonsWidget, SelectWidget, SelectMultipleWidget, ToggleButtonsWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: SelectionExample },
  { id: 'w-text', title: 'Text & HTML', category: 'Widget controls', desc: 'Text, Textarea, Password, Label, HTML and HTMLMath (KaTeX).', imports: imp('TextWidget, TextareaWidget, PasswordWidget, LabelWidget, HTMLWidget, HTMLMathWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: TextExample },
  { id: 'w-multi', title: 'Multi-value inputs', category: 'Widget controls', desc: 'TagsInput, ColorsInput, IntsInput and FloatsInput.', imports: imp('TagsInputWidget, ColorsInputWidget, IntsInputWidget, FloatsInputWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: MultiExample },
  { id: 'w-datetime', title: 'Date, time & colour', category: 'Widget controls', desc: 'ColorPicker, DatePicker, Datetime (UTC and naive), TimePicker and Time.', imports: imp('ColorPicker, DatePickerWidget, DatetimeWidget, TimePickerWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: DateTimeExample },
  { id: 'w-media', title: 'Media', category: 'Widget controls', desc: 'Image, Audio and Video widgets fed with binary comm buffers.', imports: imp('ImageWidget, AudioWidget, VideoWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: MediaWidgetsExample },
  { id: 'w-actions', title: 'Buttons, Play & Upload', category: 'Widget controls', desc: 'Button, Play and FileUpload, with a kernel that reacts to them.', imports: imp('ButtonWidget, PlayWidget, FileUploadWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: ActionsExample },
  { id: 'w-layout', title: 'Layout containers', category: 'Widget controls', desc: 'Box, HBox, VBox, GridBox (driven by a LayoutModel), Accordion, Tab and Stack.', imports: imp('BoxWidget, HBoxWidget, VBoxWidget, GridBoxWidget, AccordionWidget, TabWidget, StackWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: LayoutExample },
  { id: 'w-links', title: 'Links', category: 'Widget controls', desc: 'jslink and jsdlink: frontend-only property sync between widgets.', imports: imp('LinkWidget, DirectionalLinkWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: LinksExample },
  { id: 'w-controller', title: 'Controller', category: 'Widget controls', desc: 'Gamepad controller with its button and axis children.', imports: imp('ControllerWidget, ControllerButtonWidget, ControllerAxisWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: ControllerExample },
  { id: 'w-output', title: 'Output widget', category: 'Widget controls', desc: 'Captures outputs from the kernel, rendered with OutputArea.', imports: imp('OutputWidget', 'widgets/controls'), registry: ['widget-controls'], files: [], Example: OutputWidgetExample },
  { id: 'ipycanvas', title: 'ipycanvas', category: 'Widget controls', desc: 'A canvas that executes ipycanvas’s binary drawing-command protocol and sends mouse events back.', imports: imp('CanvasWidget, CanvasManagerWidget', 'widgets/ipycanvas'), registry: ['ipycanvas'], files: ['widgets/ipycanvas/index.ts', 'widgets/ipycanvas/ipycanvas-commands.ts'], Example: CanvasExample },
  // Isolation
  { id: 'isolated-frame', title: 'Isolated Frame', category: 'Isolation', desc: 'Sandboxed blob-URL iframe for untrusted output, with an imperative handle: render, clear, setTheme, search.', imports: imp('IsolatedFrame, type IsolatedFrameHandle', 'isolated'), registry: ['isolated-frame'], files: ['isolated/isolated-frame.tsx', 'isolated/frame-bridge.ts', 'isolated/frame-html.ts', 'isolated/comm-bridge-manager.ts', 'isolated/index.ts', 'isolated/isolated-renderer-context.tsx'], Example: IsolatedFrameExample },
  { id: 'isolation-test', title: 'IsolationTest', category: 'Isolation', desc: 'The security self-test shipped with isolated-frame: what the sandboxed frame can and cannot reach.', imports: imp('IsolationTest', 'isolated'), registry: ['isolated-frame'], files: ['isolated/IsolationTest.tsx'], Example: IsolationTestExample },
  { id: 'isolated-renderer', title: 'Isolated Renderer', category: 'Isolation', desc: 'The renderer bundle that runs inside each frame: outputs, markdown, and widgets bridged to the parent store.', imports: `import rendererCode from "../public/isolated/isolated-renderer.js?raw"`, registry: ['isolated-renderer'], files: [], Example: RendererExample },
  // Lib
  { id: 'error-boundary', title: 'Error Boundary', category: 'Lib', desc: 'Error boundary with resetKeys, plus the output and widget fallbacks.', imports: `import { ErrorBoundary } from "@/lib/error-boundary"`, registry: ['error-boundary'], files: ['lib/error-boundary.tsx', 'lib/output-error-fallback.tsx', 'lib/widget-error-fallback.tsx'], Example: ErrorBoundaryExample },
  { id: 'highlight-text', title: 'Highlight Text', category: 'Lib', desc: 'Highlights search matches in DOM text and returns a cleanup function.', imports: `import { highlightTextInDom } from "@/lib/highlight-text"`, registry: ['highlight-text'], files: ['lib/highlight-text.ts'], Example: HighlightExample },
  { id: 'dark-mode', title: 'Dark mode', category: 'Lib', desc: 'Theme detection shared by the editor, markdown and isolated frames (plus cn() in lib/utils).', imports: `import { useDarkMode } from "@/lib/dark-mode"`, registry: [], files: ['lib/dark-mode.ts', 'lib/utils.ts'], Example: DarkModeExample },
  // Icons
  { id: 'runtime-icons', title: 'Runtime Icons', category: 'Icons & cards', desc: 'SVG marks for runtimes and package managers.', imports: imp('PythonIcon, DenoIcon, UvIcon, CondaIcon, PixiIcon', 'icons/runtime-icons'), registry: ['runtime-icons'], files: ['icons/runtime-icons.tsx'], Example: IconsExample },
  { id: 'selection-card', title: 'Selection Card', category: 'Icons & cards', desc: 'Selectable cards for wizards and onboarding, with brand colours and PageDots.', imports: imp('SelectionCard, PageDots, BRAND_COLORS', 'ui/selection-card'), registry: ['selection-card'], files: ['ui/selection-card.tsx'], Example: SelectionCardExample },
  // UI primitives (shadcn, installed as registry dependencies)
  ui('accordion', 'Accordion', 'Collapsible sections: single and multiple, open, closed and disabled items.', imp('Accordion, AccordionItem, AccordionTrigger, AccordionContent', 'ui/accordion'), AccordionDemo),
  ui('avatar', 'Avatar', 'Every size with image, initials fallback and badge, plus a group with a count.', imp('Avatar, AvatarImage, AvatarFallback, AvatarBadge, AvatarGroup, AvatarGroupCount', 'ui/avatar'), AvatarDemo),
  ui('badge', 'Badge', 'Every variant, plain, with an icon and as a link.', imp('Badge', 'ui/badge'), BadgeDemo),
  ui('button', 'Button', 'Every variant × every size, icon sizes and disabled.', imp('Button', 'ui/button'), ButtonDemo),
  ui('checkbox', 'Checkbox', 'Unchecked, checked, indeterminate, disabled and invalid.', imp('Checkbox', 'ui/checkbox'), CheckboxDemo),
  ui('collapsible', 'Collapsible', 'Open, closed and disabled.', imp('Collapsible, CollapsibleTrigger, CollapsibleContent', 'ui/collapsible'), CollapsibleDemo),
  ui('command', 'Command', 'A searchable command palette (cmdk): inline, empty result, and CommandDialog.', imp('Command, CommandInput, CommandList, CommandGroup, CommandItem, CommandDialog', 'ui/command'), CommandDemo),
  ui('dialog', 'Dialog', 'A modal dialog, plus its parts held open in a static frame.', imp('Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogFooter', 'ui/dialog'), DialogDemo),
  ui('dropdown-menu', 'Dropdown Menu', 'Items, shortcuts, disabled and destructive items, checkbox and radio items — one interactive, one forced open.', imp('DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem', 'ui/dropdown-menu'), DropdownDemo),
  ui('hover-card', 'Hover Card', 'Preview content on hover — one on hover, one forced open.', imp('HoverCard, HoverCardTrigger, HoverCardContent', 'ui/hover-card'), HoverCardDemo),
  ui('input', 'Input', 'Placeholder, value, number, password, file, disabled and invalid.', imp('Input', 'ui/input'), InputDemo),
  ui('label', 'Label', 'Accessible label for a control.', imp('Label', 'ui/label'), () => <Row><div className="gx-stack gx-stack--tight gx-w240"><Label htmlFor="gx-name">Notebook name</Label><Input id="gx-name" defaultValue="Churn review" /></div><div className="gx-stack gx-stack--tight gx-w240 gx-disabled-group" data-disabled="true"><Label htmlFor="gx-name2">Disabled field label</Label><Input id="gx-name2" disabled defaultValue="locked" /></div></Row>),
  ui('popover', 'Popover', 'Floating panel anchored to a trigger — one interactive, one forced open.', imp('Popover, PopoverTrigger, PopoverContent, PopoverHeader, PopoverTitle', 'ui/popover'), PopoverDemo),
  ui('progress', 'Progress', '0%, 25%, 72%, 100% and a live bar.', imp('Progress', 'ui/progress'), ProgressDemo),
  ui('radio-group', 'Radio Group', 'Selected, unselected, a disabled item and a disabled group.', imp('RadioGroup, RadioGroupItem', 'ui/radio-group'), RadioDemo),
  ui('select', 'Select', 'With a value, placeholder, small, disabled and invalid triggers.', imp('Select, SelectTrigger, SelectValue, SelectContent, SelectItem', 'ui/select'), SelectDemo),
  ui('slider', 'Slider', 'Single, range, stepped, disabled and vertical.', imp('Slider', 'ui/slider'), SliderDemo),
  ui('tabs', 'Tabs', 'Default and line list variants, disabled trigger and vertical orientation.', imp('Tabs, TabsList, TabsTrigger, TabsContent', 'ui/tabs'), TabsDemo),
  ui('textarea', 'Textarea', 'Value, placeholder, disabled and invalid.', imp('Textarea', 'ui/textarea'), TextareaDemo),
  ui('toggle', 'Toggle', 'Both variants × three sizes, off, on and disabled.', imp('Toggle', 'ui/toggle'), ToggleDemo),
  ui('toggle-group', 'Toggle Group', 'Single and multiple, sizes, a disabled item and a disabled group.', imp('ToggleGroup, ToggleGroupItem', 'ui/toggle-group'), ToggleGroupDemo),
]
