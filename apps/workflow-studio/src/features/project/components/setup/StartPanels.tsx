/**
 * Inside a project: the first action of each start card, opened in place under the picker. Every action records
 * the start (`setup.choose`) before it leaves, so the next steps follow from it.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { AppWindow, Code2, Database, FileSearch, Mail, Plus, SlidersHorizontal, Sparkles, WandSparkles, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Kbd } from '@/shared/components/ui/kbd'
import { Segmented } from '@/shared/components/ui/segmented'
import { DatasetDropZone } from '@/features/datasets/components/DatasetDropZone'
import type { DatasetItem } from '@/features/datasets/lib/types'
import { TemplatePreviewSheet } from '@/features/templates/TemplatePreviewSheet'
import { useTemplates } from '@/features/templates/hooks'
import { useProjectCtx } from '../../ProjectContext'
import type { SetupState } from '../../hooks/useSetup'
import { useCreateInterface, useDatasetStarters, useOpenWorkflow } from '../../hooks/useSetupActions'
import { guessTarget, type AiTarget, type StartKind } from '../../lib/nextSteps'
import { STARTERS } from '../../lib/starters'
import { seedHomeTemplate } from '../../home/startActions'
import type { HomeTemplate } from '../../home/templates'
import { PromptBox, TemplateChooser } from './parts'

const TARGET_OPTIONS: Array<{ key: AiTarget; label: string; testId: string }> = [
  { key: 'workflow', label: 'Workflow', testId: 'setup-ai-target-workflow' },
  { key: 'backend', label: 'Backend', testId: 'setup-ai-target-backend' },
  { key: 'interface', label: 'Interface', testId: 'setup-ai-target-interface' },
]

export function GuideStartPanel({ setup, kind }: { setup: SetupState; kind: StartKind }) {
  return (
    <div className="flex flex-col gap-2" data-testid={`setup-panel-${kind}`}>
      {kind === 'interface' && <InterfacePanel setup={setup} />}
      {kind === 'backend' && <BackendPanel setup={setup} />}
      {kind === 'workflow' && <WorkflowPanel setup={setup} />}
      {kind === 'dataset' && <DatasetPanel setup={setup} />}
      {kind === 'template' && <TemplatePanel setup={setup} />}
      {kind === 'ai' && <AiPanel setup={setup} />}
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-[12.5px] text-muted-foreground">{children}</p>
}

function InterfacePanel({ setup }: { setup: SetupState }) {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  const ui = useCreateInterface()
  const [prompt, setPrompt] = useState('')
  const go = () => {
    setup.choose('interface')
    void ui.create(prompt)
  }
  return (
    <>
      <Note>Start with the page. Routes it calls that don't exist yet become stubs; give them a handler whenever you like.</Note>
      <PromptBox value={prompt} onChange={setPrompt} onSubmit={go} label="Describe the interface" placeholder='Describe the page… e.g. "A form to file refund requests and a table of open ones."'>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setup.choose('interface')
            navigate({ to: '/p/$project', params: { project: slug }, search: { dialog: 'create-ui' } })
          }}
          data-testid="setup-create-ui"
        >
          <SlidersHorizontal /> More options
        </Button>
        <div className="flex-1" />
        <Button size="sm" disabled={ui.busy} onClick={go} data-testid="setup-create-interface">
          <AppWindow /> {ui.busy ? 'Creating…' : 'Create interface'}
        </Button>
      </PromptBox>
    </>
  )
}

function BackendPanel({ setup }: { setup: SetupState }) {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const go = () => {
    if (!prompt.trim()) return
    setup.choose('backend')
    navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: '~new' }, search: { prompt: prompt.trim(), generate: 1 } })
  }
  return (
    <>
      <Note>
        Describe the API. You get a Python module using node functions; routes appear when you save.{' '}
        <Badge variant="ai">
          <Sparkles /> AI
        </Badge>
      </Note>
      <PromptBox
        value={prompt}
        onChange={setPrompt}
        onSubmit={go}
        label="Describe your API"
        testId="setup-prompt"
        placeholder='Describe your API… e.g. "Store refunds in a table. POST /refunds creates one, GET /refunds/stats totals them by reason."'
      >
        {STARTERS.map((s) => (
          <Button key={s.key} variant="outline" size="sm" onClick={() => setPrompt(s.prompt)} data-testid={`starter-${s.key}`}>
            <s.icon /> {s.label}
          </Button>
        ))}
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setup.choose('backend')
            navigate({ to: '/p/$project', params: { project: slug }, search: { dialog: 'new-module' } })
          }}
          data-testid="setup-write-myself"
        >
          <Code2 /> Write it myself
        </Button>
        <Button variant="ai" size="sm" disabled={!prompt.trim()} onClick={go} data-testid="setup-generate">
          <WandSparkles /> Generate <Kbd>⏎</Kbd>
        </Button>
      </PromptBox>
    </>
  )
}

function WorkflowPanel({ setup }: { setup: SetupState }) {
  const open = useOpenWorkflow()
  return (
    <>
      <Note>Steps on a canvas. Start from a blank one, or describe it and let the AI lay out the nodes with you.</Note>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setup.choose('workflow')
            open()
          }}
          data-testid="setup-workflow-blank"
        >
          <Workflow /> Blank workflow
        </Button>
        <Button
          variant="ai"
          size="sm"
          onClick={() => {
            setup.choose('workflow')
            open({ ai: true })
          }}
          data-testid="setup-workflow-ai"
        >
          <Sparkles /> Build with AI
        </Button>
      </div>
    </>
  )
}

function DatasetPanel({ setup }: { setup: SetupState }) {
  const { pid } = useProjectCtx()
  const ds = useDatasetStarters()
  const [uploaded, setUploaded] = useState<DatasetItem | null>(null)
  const dataset = uploaded ?? setup.latestDataset
  return (
    <>
      <Note>Drop a CSV, Parquet or Excel file. Then profile it, or have a report emailed to you.</Note>
      <DatasetDropZone
        pid={pid}
        onUploaded={(d) => {
          setUploaded(d)
          setup.choose('dataset')
        }}
      />
      {dataset && (
        <div className="flex flex-wrap items-center gap-2 rounded-ctl bg-raise px-3 py-2.5 shadow-raise-sm" data-testid="setup-dataset-ready">
          <Database className="size-3.5 shrink-0 text-hue-data" />
          <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{dataset.name}</span>
          <Button
            variant="outline"
            size="sm"
            disabled={!!ds.busy}
            onClick={() => {
              setup.choose('dataset')
              void ds.profile(dataset)
            }}
            data-testid="setup-profile-dataset"
          >
            <FileSearch /> {ds.busy === 'profile' ? 'Installing…' : 'Profile it'}
          </Button>
          <Button
            size="sm"
            disabled={!!ds.busy}
            onClick={() => {
              setup.choose('dataset')
              void ds.report(dataset)
            }}
            data-testid="setup-report-dataset"
          >
            <Mail /> {ds.busy === 'report' ? 'Installing…' : 'Email me a report'}
          </Button>
        </div>
      )}
    </>
  )
}

function TemplatePanel({ setup }: { setup: SetupState }) {
  const { project, pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const templates = useTemplates(pid)
  // a template chosen on the home page opens straight into its preview
  const [preview, setPreview] = useState<string | null>(setup.start === 'template' ? (setup.onboarding.template ?? null) : null)
  const [busy, setBusy] = useState<string | null>(null)
  const target = { id: pid, slug, name: project.name }

  const applyHome = async (t: HomeTemplate) => {
    setBusy(t.key)
    try {
      setup.choose('template')
      await seedHomeTemplate(pid, t)
      navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: t.module } })
    } catch (e) {
      toast.error(`Couldn't use the template: ${errorMessage(e)}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <Note>Ready-made workflows and backends. The preview shows what each needs (secrets, variables, hosts) before anything is installed.</Note>
      <TemplateChooser
        engine={templates.data ?? []}
        loading={templates.isLoading}
        busy={busy}
        onEngine={(t) => {
          setup.choose('template', { template: t.slug })
          setPreview(t.slug)
        }}
        onHome={(t) => void applyHome(t)}
      />
      <TemplatePreviewSheet
        slug={preview}
        onClose={() => {
          setPreview(null)
          if (setup.onboarding.template) setup.choose('template', { template: null })
        }}
        target={target}
        projects={[target]}
        onTargetChange={() => {}}
      />
    </>
  )
}

function AiPanel({ setup }: { setup: SetupState }) {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  const open = useOpenWorkflow()
  const ui = useCreateInterface()
  const [prompt, setPrompt] = useState('')
  const [picked, setPicked] = useState<AiTarget | null>(setup.onboarding.target ?? null)
  const target = picked ?? guessTarget(prompt)
  const go = () => {
    const text = prompt.trim()
    if (!text) return
    setup.choose('ai', { target })
    if (target === 'workflow') open({ prompt: text })
    else if (target === 'backend')
      navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: '~new' }, search: { prompt: text, generate: 1 } })
    else void ui.create(text)
  }
  return (
    <>
      <Note>Say what you want. The chip is a guess from your words; change it if it's wrong.</Note>
      <PromptBox
        value={prompt}
        onChange={setPrompt}
        onSubmit={go}
        label="Describe what you want"
        testId="setup-ai-prompt"
        placeholder='e.g. "Every morning, email me yesterday&apos;s failed payments."'
      >
        <Segmented<AiTarget>
          value={target}
          onChange={setPicked}
          options={TARGET_OPTIONS}
          size="xs"
          aria-label="Build it as"
          data-testid="setup-ai-target"
        />
        <div className="flex-1" />
        <Button variant="ai" size="sm" disabled={!prompt.trim() || ui.busy} onClick={go} data-testid="setup-ai-go">
          {target === 'interface' ? <Plus /> : <WandSparkles />} Build it <Kbd>⏎</Kbd>
        </Button>
      </PromptBox>
    </>
  )
}
