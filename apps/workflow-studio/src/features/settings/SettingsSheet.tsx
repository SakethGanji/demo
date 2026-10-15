/**
 * Project settings sheet (UI-DESIGN §3.7, `settings.png`): one scrolling sheet with section
 * anchors General · Environments · Egress · API keys · Developer · Danger. The segmented control
 * highlights the section in view and jumps between them (`?sheet=settings&section=…`).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Settings2 } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Segmented } from '@/shared/components/ui/segmented'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { isSection, SECTIONS, type SectionKey } from './common'
import { GeneralSection, useGeneralForm } from './GeneralSection'
import { EnvironmentsSection } from './EnvironmentsSection'
import { EgressSection } from './EgressSection'
import { ApiKeysSection } from './ApiKeysSection'
import { DeveloperSection } from './DeveloperSection'
import { DangerSection } from './DangerSection'

/** The floating sheet look shared with the Release sheet (mockup `.sheet`). */
export const floatingSheetCls =
  'gap-0 overflow-hidden rounded-[14px] border border-panel-border bg-card p-0 shadow-2xl data-[side=right]:inset-y-3 data-[side=right]:right-3 data-[side=right]:h-auto data-[side=right]:border data-[side=right]:sm:max-w-[calc(100vw-24px)] data-[side=right]:max-w-[calc(100vw-24px)]'

export function SettingsSheet({ open, section, onClose }: { open: boolean; section?: string; onClose: () => void }) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" showCloseButton className={`${floatingSheetCls} data-[side=right]:w-[560px]`} data-testid="settings-sheet">
        {open && <SettingsBody section={section} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  )
}

function SettingsBody({ section, onClose }: { section?: string; onClose: () => void }) {
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement>(null)
  const refs = useRef<Partial<Record<SectionKey, HTMLElement | null>>>({})
  const [active, setActive] = useState<SectionKey>(isSection(section) ? section : 'general')
  const jumping = useRef(false)
  const general = useGeneralForm(true)

  const jump = useCallback((k: SectionKey, smooth = true) => {
    const el = refs.current[k]
    const sc = scrollRef.current
    if (!el || !sc) return
    jumping.current = true
    setActive(k)
    sc.scrollTo({ top: el.offsetTop - 16, behavior: smooth ? 'smooth' : 'auto' })
    setTimeout(() => (jumping.current = false), smooth ? 500 : 50)
  }, [])

  // Deep link (`section=keys`) and later URL changes.
  const navigated = useRef<string | null>(null)
  useEffect(() => {
    if (!isSection(section) || navigated.current === section) return
    requestAnimationFrame(() => jump(section, false))
  }, [section, jump])

  const onScroll = () => {
    if (jumping.current) return
    const sc = scrollRef.current
    if (!sc) return
    let cur: SectionKey = 'general'
    for (const s of SECTIONS) {
      const el = refs.current[s.key]
      if (el && el.offsetTop - 40 <= sc.scrollTop) cur = s.key
    }
    if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2) cur = 'danger'
    setActive(cur)
  }

  const select = (k: SectionKey) => {
    jump(k)
    navigated.current = k
    navigate({ to: '.', search: (s: Record<string, unknown>) => ({ ...s, section: k }), replace: true } as never)
  }

  const reg = (k: SectionKey) => (el: HTMLElement | null) => {
    refs.current[k] = el
  }

  return (
    <>
      <div className="border-b border-border px-[18px] pb-3 pt-4">
        <div className="flex items-center gap-2 pr-8">
          <Settings2 className="size-3.5" />
          <SheetTitle className="text-[15px] font-semibold tracking-[-0.01em]">Project settings</SheetTitle>
        </div>
        <SheetDescription className="sr-only">Name, slug, environments, egress, API keys, developer tools and deletion</SheetDescription>
        <Segmented<SectionKey>
          className="mt-3"
          value={active}
          onChange={select}
          aria-label="Settings sections"
          data-testid="settings-sections"
          options={SECTIONS.map((s) => ({ key: s.key, label: s.label, testId: `settings-section-${s.key}` }))}
        />
      </div>
      <div ref={scrollRef} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden" data-testid="settings-panel">
        <div className="flex flex-col px-[18px] pb-8 pt-4">
          <section ref={reg('general')} id="settings-general" className="scroll-mt-4">
            <GeneralSection form={general} />
          </section>
          <Divider />
          <section ref={reg('environments')} id="settings-environments">
            <EnvironmentsSection open />
          </section>
          <Divider />
          <section ref={reg('egress')} id="settings-egress">
            <EgressSection open />
          </section>
          <Divider />
          <section ref={reg('keys')} id="settings-keys">
            <ApiKeysSection />
          </section>
          <Divider />
          <section ref={reg('developer')} id="settings-developer">
            <DeveloperSection />
          </section>
          <Divider />
          <section ref={reg('danger')} id="settings-danger" className="min-h-[40%]">
            <DangerSection />
          </section>
        </div>
      </div>
      <div className="flex items-center gap-2 border-t border-border bg-[color-mix(in_srgb,var(--muted)_40%,var(--card))] px-[18px] py-3">
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          {general.dirty ? 'Unsaved changes in General' : 'Other sections save on their own'}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            if (general.dirty) general.reset()
            else onClose()
          }}
          data-testid="settings-cancel"
        >
          {general.dirty ? 'Cancel' : 'Close'}
        </Button>
        {general.dirty && (
          <Button size="sm" disabled={!general.canSave} onClick={() => void general.save()} data-testid="settings-save">
            {general.saveLabel}
          </Button>
        )}
      </div>
    </>
  )
}

function Divider() {
  return <div className="my-5 h-px bg-border" />
}
