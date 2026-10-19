/**
 * "Start something new" on the home page: the chosen card's first action, opened in place under the picker.
 * Each action creates the project (`useStartProject`), records how it started, and opens where that start leads.
 */
import { useRef, useState } from 'react'
import { AppWindow, Code2, FileUp, Sparkles, WandSparkles, Workflow } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Kbd } from '@/shared/components/ui/kbd'
import { Segmented } from '@/shared/components/ui/segmented'
import { Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { DATASET_ACCEPT } from '@/features/datasets/lib/datasets'
import { useTemplates } from '@/features/templates/hooks'
import { guessTarget, type AiTarget, type StartKind } from '../lib/nextSteps'
import { STARTERS } from '../lib/starters'
import { PromptBox, TemplateChooser } from '../components/setup/parts'
import type { useStartProject } from './useStartProject'

type Start = ReturnType<typeof useStartProject>

const TARGET_OPTIONS: Array<{ key: AiTarget; label: string; testId: string }> = [
  { key: 'workflow', label: 'Workflow', testId: 'home-ai-target-workflow' },
  { key: 'backend', label: 'Backend', testId: 'home-ai-target-backend' },
  { key: 'interface', label: 'Interface', testId: 'home-ai-target-interface' },
]

export function HomeStartPanel({ kind, start }: { kind: StartKind; start: Start }) {
  return (
    <Tile className="flex flex-col gap-2 p-3" data-testid={`home-start-panel-${kind}`}>
      {kind === 'interface' && <InterfacePanel start={start} />}
      {kind === 'backend' && <BackendPanel start={start} />}
      {kind === 'workflow' && <WorkflowPanel start={start} />}
      {kind === 'dataset' && <DatasetPanel start={start} />}
      {kind === 'template' && <TemplatePanel start={start} />}
      {kind === 'ai' && <AiPanel start={start} />}
    </Tile>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="px-1 text-[12.5px] text-muted-foreground">{children}</p>
}

function InterfacePanel({ start }: { start: Start }) {
  const [prompt, setPrompt] = useState('')
  const go = () => start.fromInterface(prompt)
  return (
    <>
      <Note>A new project with one interface and no backend. Routes it calls become stubs until you give them a handler.</Note>
      <PromptBox value={prompt} onChange={setPrompt} onSubmit={go} label="Describe the interface" placeholder="Describe the page… (optional)">
        <div className="flex-1" />
        <Button size="sm" disabled={!!start.busy} onClick={go} data-testid="home-create-interface">
          <AppWindow /> {start.busy === 'interface' ? 'Creating…' : 'Create interface'}
        </Button>
      </PromptBox>
    </>
  )
}

function BackendPanel({ start }: { start: Start }) {
  const [prompt, setPrompt] = useState('')
  const go = () => prompt.trim() && start.fromBackend(prompt)
  return (
    <>
      <Note>Describe the API and get a Python module, or start an empty one.</Note>
      <PromptBox value={prompt} onChange={setPrompt} onSubmit={go} label="Describe your API" placeholder="Describe your API…">
        {STARTERS.map((s) => (
          <Button key={s.key} variant="outline" size="sm" onClick={() => setPrompt(s.prompt)} data-testid={`home-starter-${s.key}`}>
            <s.icon /> {s.label}
          </Button>
        ))}
        <div className="flex-1" />
        <Button variant="ghost" size="sm" disabled={!!start.busy} onClick={() => start.fromBackend('')} data-testid="home-write-myself">
          <Code2 /> Write it myself
        </Button>
        <Button variant="ai" size="sm" disabled={!prompt.trim() || !!start.busy} onClick={go} data-testid="home-generate">
          <WandSparkles /> {start.busy === 'backend' ? 'Creating…' : 'Generate'} <Kbd>⏎</Kbd>
        </Button>
      </PromptBox>
    </>
  )
}

function WorkflowPanel({ start }: { start: Start }) {
  return (
    <>
      <Note>A new project with a workflow canvas.</Note>
      <div className="flex flex-wrap items-center gap-2 px-1">
        <Button variant="outline" size="sm" disabled={!!start.busy} onClick={() => start.fromWorkflow()} data-testid="home-workflow-blank">
          <Workflow /> Blank workflow
        </Button>
        <Button variant="ai" size="sm" disabled={!!start.busy} onClick={() => start.fromWorkflow({ ai: true })} data-testid="home-workflow-ai">
          <Sparkles /> Build with AI
        </Button>
      </div>
    </>
  )
}

function DatasetPanel({ start }: { start: Start }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const send = (f: File | undefined) => f && !start.busy && start.fromDataset(f)
  return (
    <>
      <Note>A new project with this file as its first dataset. Then profile it, or have a report emailed to you.</Note>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          send(e.dataTransfer.files[0])
        }}
        className={cn(
          'flex flex-col items-center gap-1 rounded-ctl bg-recess px-4 py-6 text-[12.5px] text-muted-foreground transition-shadow duration-(--d-fast) focus-visible:shadow-focus focus-visible:outline-none motion-reduce:transition-none',
          over && 'shadow-[0_0_0_1.5px_var(--ring)]',
        )}
        data-testid="home-dataset-drop"
      >
        <FileUp className="size-4 text-hue-data" />
        {start.busy === 'dataset' ? 'Importing…' : 'Drop a CSV, Parquet or Excel file, or click to choose'}
      </button>
      <input
        ref={input}
        type="file"
        accept={DATASET_ACCEPT}
        className="hidden"
        onChange={(e) => {
          send(e.target.files?.[0])
          e.target.value = ''
        }}
        data-testid="home-dataset-input"
      />
    </>
  )
}

function TemplatePanel({ start }: { start: Start }) {
  const templates = useTemplates(undefined)
  return (
    <>
      <Note>A new project from a template. Engine templates open with a preview of what they need before anything installs.</Note>
      <TemplateChooser engine={templates.data ?? []} loading={templates.isLoading} busy={start.busy} onEngine={(t) => start.fromTemplate(t)} onHome={(t) => start.fromTemplate(t)} />
    </>
  )
}

function AiPanel({ start }: { start: Start }) {
  const [prompt, setPrompt] = useState('')
  const [picked, setPicked] = useState<AiTarget | null>(null)
  const target = picked ?? guessTarget(prompt)
  const go = () => {
    const text = prompt.trim()
    if (!text || start.busy) return
    if (target === 'workflow') start.fromWorkflow({ prompt: text })
    else if (target === 'backend') start.fromPrompt(text)
    else start.fromInterface(text, true)
  }
  return (
    <>
      <Note>Say what you want. The chip is a guess from your words; change it if it's wrong.</Note>
      <PromptBox value={prompt} onChange={setPrompt} onSubmit={go} label="Describe a project" testId="home-describe-input" placeholder="A refunds API that takes Stripe webhooks and flags refunds over $500…">
        <Segmented<AiTarget> value={target} onChange={setPicked} options={TARGET_OPTIONS} size="xs" aria-label="Build it as" data-testid="home-ai-target" />
        <div className="flex-1" />
        <Button variant="ai" size="sm" disabled={!prompt.trim() || !!start.busy} onClick={go} data-testid="home-describe-generate">
          <Sparkles /> {start.busy ? 'Creating…' : 'Build it'} <Kbd>⏎</Kbd>
        </Button>
      </PromptBox>
    </>
  )
}
