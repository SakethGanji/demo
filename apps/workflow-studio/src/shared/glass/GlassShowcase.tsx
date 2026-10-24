/** The glass layer on `/__design#glass`: every primitive, with test ids the e2e spec drives. */
import { useState } from 'react'
import {
  CheckStrip, Chip, DataTablePreview, DiffBlock, Drawer, GlassPanel, GlassSheet, KindLabel, PageBranch, PageDoc, PageForEach,
  PageLoop, PageParallel, PageSection, PageStep, Palette, SplitButton, StatusMark, StepResult, STEP_STATUSES, ValueToken, ViewSwitch,
} from './index'

const ROWS = [
  { id: 'W-1042', amount: 18250, status: 'stuck', since: '07:12' },
  { id: 'W-1043', amount: 940.5, status: 'stuck', since: '07:31' },
  { id: 'W-1051', amount: null, status: 'pending', since: '08:02' },
  { id: 'W-1060', amount: 120, status: 'stuck', since: '08:40' },
]

export function GlassShowcase() {
  const [view, setView] = useState<'page' | 'editor' | 'definition'>('page')
  const [limit, setLimit] = useState('10000')
  const [iter, setIter] = useState(0)
  const [sheet, setSheet] = useState<null | 'right' | 'wide'>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [tab, setTab] = useState<'runs' | 'logs' | 'problems' | 'tests'>('runs')
  const [palette, setPalette] = useState(false)
  const [last, setLast] = useState('—')

  return (
    <section id="glass" className="flex flex-col gap-3" data-testid="glass-showcase">
      <h2 className="text-[12.5px] font-medium text-muted-foreground">Glass layer (src/shared/glass)</h2>
      <div className="glass-page overflow-hidden rounded-[18px] p-4 sm:p-6" data-testid="glass-sky">
        <div className="grid gap-4">
          <GlassPanel padding="md" className="flex flex-wrap items-center gap-3" data-testid="glass-controls">
            <ViewSwitch
              aria-label="View"
              value={view}
              onChange={setView}
              options={[
                { value: 'page', label: 'Page' },
                { value: 'editor', label: 'Editor' },
                { value: 'definition', label: 'Definition' },
              ]}
            />
            <SplitButton
              data-testid="glass-split"
              label="Run"
              onClick={() => setLast('run')}
              menuLabel="Run options"
              items={[
                { id: 'payload', label: 'Run with test payload', hint: '⇧↵', onSelect: () => setLast('run-payload') },
                { id: 'from', label: 'Run from selected step', onSelect: () => setLast('run-from') },
              ]}
            />
            <button type="button" className="gl-btn" data-variant="ghost" onClick={() => setPalette(true)} data-testid="glass-open-palette">
              ⌘K
            </button>
            <button type="button" className="gl-btn" onClick={() => setSheet('right')} data-testid="glass-open-sheet">
              Side sheet
            </button>
            <button type="button" className="gl-btn" onClick={() => setSheet('wide')} data-testid="glass-open-wide">
              Wide sheet
            </button>
            <span className="kind-label" data-testid="glass-last-action">
              last: {last}
            </span>
          </GlassPanel>

          <GlassPanel padding="md" className="flex flex-wrap items-center gap-2.5" data-testid="glass-chips">
            <Chip tone="ok" dot>v4 · Live</Chip>
            <Chip tone="warn">needs you</Chip>
            <Chip tone="prod" dot="live">writes · PRODUCTION</Chip>
            <Chip tone="ai">needs AI</Chip>
            <Chip tone="brand" size="sm">kept</Chip>
            <Chip mono size="sm">dev</Chip>
            <span className="mx-1 h-5 w-px bg-hairline" />
            {STEP_STATUSES.map((s) => (
              <span key={s} className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <StatusMark status={s} /> {s}
              </span>
            ))}
            <span className="mx-1 h-5 w-px bg-hairline" />
            <CheckStrip summary data-testid="glass-check-strip" results={['pass', 'pass', 'fail', 'pass', 'pass', 'skip', 'pass', 'pass']} />
            <KindLabel kind="query" parts={['bank-prod', '120 ms']} />
          </GlassPanel>

          <div className="grid gap-4 lg:grid-cols-2">
            <GlassPanel variant="well" padding="sm">
              <DiffBlock
                data-testid="glass-diff"
                title="bank.wires · 1 row"
                lines={[
                  { kind: 'ctx', k: 'id', text: 'W-1042' },
                  { kind: 'del', k: 'status', text: 'stuck' },
                  { kind: 'add', k: 'status', text: 'released' },
                ]}
              />
            </GlassPanel>
            <GlassPanel variant="raised" padding="sm">
              <DataTablePreview data-testid="glass-table" rows={ROWS} total={148} onMore={() => setLast('more-rows')} />
            </GlassPanel>
          </div>

          <GlassPanel padding="none" data-testid="glass-page-panel">
            <PageDoc
              data-testid="glass-page-doc"
              eyebrow={<Chip tone="ok" size="sm" dot>v4 · Live</Chip>}
              title="Release stuck wires"
              byline={<span>Every weekday at 08:00 · 12 runs this week · used by 2</span>}
            >
              <PageSection id="glass-s1" num={1} title="Find the stuck wires" note="3 steps">
                <PageStep
                  data-testid="glass-step-query"
                  status="ok"
                  kind="query"
                  kindParts={['bank-prod']}
                  label="Find stuck wires"
                  meta="120 ms"
                  num={1}
                  onPath
                  sentence={
                    <>
                      Find wires stuck for more than <ValueToken value={limit} label="minutes" path="parameters.minutes" onCommit={setLimit} hint="Rerun from here after changing" /> minutes in{' '}
                      <ValueToken value="bank.wires" label="table" />.
                    </>
                  }
                  result={<StepResult tone="ok">4 rows</StepResult>}
                  annotation={<span>Priya · as svc-ops · via bank-prod read-only; 120 ms</span>}
                  actions={<button type="button" className="gl-btn" data-size="sm">Rerun</button>}
                >
                  <DataTablePreview rows={ROWS} total={4} />
                </PageStep>
                <PageBranch
                  data-testid="glass-branch"
                  status="ok"
                  label="Route by amount"
                  num={2}
                  onPath
                  sentence={<>If the amount is over <ValueToken value="10,000" label="threshold" /></>}
                  cases={[
                    {
                      id: 'big',
                      cond: '> 10k',
                      label: 'Ask a manager to approve',
                      taken: true,
                      summary: '1 step',
                      children: (
                        <PageStep status="need" kind="approval" label="Manager" sentence="Ask Maria to approve the release." annotation="waiting since 09:42" last />
                      ),
                    },
                    {
                      id: 'small',
                      cond: 'else',
                      label: 'Release automatically',
                      summary: '1 step',
                      children: <PageStep status="skip" kind="http" sentence="POST /wires/release" dim last />,
                    },
                  ]}
                />
                <PageLoop
                  data-testid="glass-loop"
                  status="ok"
                  label="Notify"
                  num={3}
                  onPath
                  sentence="For each wire, email the owner."
                  title="Each wire"
                  iterations={[{ status: 'ok', label: 'W-1042' }, { status: 'ok', label: 'W-1043' }, { status: 'fail', label: 'W-1051' }]}
                  value={iter}
                  onChange={setIter}
                >
                  <PageStep status={iter === 2 ? 'fail' : 'ok'} kind="email" sentence={`Email the owner of ${['W-1042', 'W-1043', 'W-1051'][iter]}.`} annotation={iter === 2 ? 'SMTP 550 mailbox unavailable' : 'sent 09:43'} last />
                </PageLoop>
              </PageSection>
              <PageSection id="glass-s2" num={2} title="Release">
                <PageParallel
                  data-testid="glass-parallel"
                  status="ok"
                  label="Fan out"
                  sentence="At the same time, update the ledger and post to Teams."
                  join="Both lanes join here"
                  lanes={[
                    { id: 'ledger', label: 'Ledger', status: 'ok', children: <PageStep status="ok" kind="postgres" sentence="Mark released." last /> },
                    { id: 'teams', label: 'Teams', status: 'ok', children: <PageStep status="ok" kind="http" sentence="Post a summary." last /> },
                  ]}
                />
                <PageForEach
                  data-testid="glass-foreach"
                  status="fail"
                  label="Release each"
                  sentence="Release each wire through the payments API."
                  total={150}
                  ok={148}
                  failed={2}
                  rows={[
                    { index: 17, label: 'W-1051', status: 'fail', why: '409 already settled' },
                    { index: 88, label: 'W-1102', status: 'fail', why: 'timeout after 30 s' },
                  ]}
                />
                <PageStep data-testid="glass-step-stale" status="stale" kind="note" stale="from run 1" sentence="Summarise what changed for the audit log." />
              </PageSection>
            </PageDoc>
          </GlassPanel>

          <Drawer
            data-testid="glass-drawer"
            open={drawerOpen}
            onOpenChange={setDrawerOpen}
            tab={tab}
            onTabChange={setTab}
            lead={<StatusMark status="ok" size="sm" />}
            height={120}
            tabs={[
              { id: 'runs', label: 'Runs', count: 12 },
              { id: 'logs', label: 'Logs' },
              { id: 'problems', label: 'Problems', count: 2, tone: 'bad' },
              { id: 'tests', label: 'Tests', count: 5 },
            ]}
          >
            <div className="py-2 text-[13px] text-muted-foreground" data-testid="glass-drawer-body">
              {tab} panel
            </div>
          </Drawer>
        </div>
      </div>

      <GlassSheet
        data-testid="glass-sheet"
        open={sheet !== null}
        onOpenChange={(o) => !o && setSheet(null)}
        side={sheet ?? 'right'}
        title={sheet === 'wide' ? 'Find stuck wires' : 'Inspector'}
        panes={
          sheet === 'wide'
            ? [
                { title: 'Input', content: <span className="text-[12.5px]">from trigger</span> },
                { title: 'Setup', content: <span className="text-[12.5px]">SQL</span> },
                { title: 'Output', content: <DataTablePreview rows={ROWS} /> },
              ]
            : undefined
        }
      >
        {sheet === 'right' ? <p className="text-[13px] text-muted-foreground">Setup · Data · Settings</p> : undefined}
      </GlassSheet>

      <Palette
        data-testid="glass-palette"
        open={palette}
        onOpenChange={setPalette}
        items={[
          { id: 'query', label: 'Query a database', group: 'Add a cell', keywords: 'sql select', onSelect: () => setLast('palette-query') },
          { id: 'http', label: 'Call an API', group: 'Add a cell', onSelect: () => setLast('palette-http') },
          { id: 'ask', label: 'Ask AI', group: 'Add a cell', disabled: true, reason: 'AI is not configured', onSelect: () => setLast('palette-ask') },
          { id: 'tpl', label: 'Open template gallery', group: 'Go', hint: 'G T', onSelect: () => setLast('palette-tpl') },
        ]}
      />
    </section>
  )
}
