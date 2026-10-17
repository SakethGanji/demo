/**
 * Project settings sheet (UI-DESIGN §3.7, `settings.png`): one scrolling sheet with section
 * anchors General · Environments · Egress · API keys · Developer · Danger. The segmented control
 * highlights the section in view and jumps between them (`?sheet=settings&section=…`).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/shared/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/shared/components/ui/sheet";
import { cn } from "@/shared/lib/utils";
import { isSection, SECTIONS, type SectionKey } from "./sections";
import { GeneralSection, useGeneralForm } from "./GeneralSection";
import { EnvironmentsSection } from "./EnvironmentsSection";
import { EgressSection } from "./EgressSection";
import { ApiKeysSection } from "./ApiKeysSection";
import { DeveloperSection } from "./DeveloperSection";
import { DangerSection } from "./DangerSection";

/** The floating sheet look shared with the Release sheet (mockup `.sheet`). */
export const floatingSheetCls =
  "gap-0 overflow-hidden p-0 data-[side=right]:inset-y-3 data-[side=right]:right-3 data-[side=right]:h-auto data-[side=right]:sm:max-w-[calc(100vw-24px)] data-[side=right]:max-w-[calc(100vw-24px)]";

export function SettingsSheet({
  open,
  section,
  onClose,
}: {
  open: boolean;
  section?: string;
  onClose: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        side="right"
        showCloseButton
        className={`${floatingSheetCls} data-[side=right]:w-[720px]`}
        data-testid="settings-sheet"
      >
        {open && <SettingsBody section={section} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  );
}

function SettingsBody({
  section,
  onClose,
}: {
  section?: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  const refs = useRef<Partial<Record<SectionKey, HTMLElement | null>>>({});
  const [active, setActive] = useState<SectionKey>(
    isSection(section) ? section : "general",
  );
  const jumping = useRef(false);
  const general = useGeneralForm(true);

  const jump = useCallback((k: SectionKey, smooth = true) => {
    const el = refs.current[k];
    const sc = scrollRef.current;
    if (!el || !sc) return;
    jumping.current = true;
    setActive(k);
    sc.scrollTo({
      top: el.offsetTop - 4,
      behavior: smooth ? "smooth" : "auto",
    });
    setTimeout(() => (jumping.current = false), smooth ? 500 : 50);
  }, []);

  // Deep link (`section=keys`) and later URL changes.
  const navigated = useRef<string | null>(null);
  useEffect(() => {
    if (!isSection(section) || navigated.current === section) return;
    requestAnimationFrame(() => jump(section, false));
  }, [section, jump]);

  const onScroll = () => {
    if (jumping.current) return;
    const sc = scrollRef.current;
    if (!sc) return;
    let cur: SectionKey = "general";
    for (const s of SECTIONS) {
      const el = refs.current[s.key];
      if (el && el.offsetTop - 40 <= sc.scrollTop) cur = s.key;
    }
    if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2) cur = "danger";
    setActive(cur);
  };

  const select = (k: SectionKey) => {
    jump(k);
    navigated.current = k;
    navigate({
      to: ".",
      search: (s: Record<string, unknown>) => ({ ...s, section: k }),
      replace: true,
    } as never);
  };

  const reg = (k: SectionKey) => (el: HTMLElement | null) => {
    refs.current[k] = el;
  };

  const activeIndex = Math.max(
    0,
    SECTIONS.findIndex((s) => s.key === active),
  );

  return (
    <>
      <div className="flex h-[52px] shrink-0 items-center gap-2 pl-5 pr-14">
        <SheetTitle className="text-[15px] font-semibold tracking-[-0.012em]">
          Project settings
        </SheetTitle>
        <SheetDescription className="sr-only">
          Name, slug, environments, egress, API keys, developer tools and
          deletion
        </SheetDescription>
      </div>
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Settings sections"
          className="relative w-[156px] shrink-0 px-3 pt-1"
          data-testid="settings-sections"
        >
          <div className="relative flex flex-col gap-0.5">
            <span
              aria-hidden
              className="raise pointer-events-none absolute inset-x-0 top-0 h-[30px] rounded-ctl transition-transform duration-(--d-base) ease-(--ease-io)"
              style={{ transform: `translateY(${activeIndex * 32}px)` }}
            />
            {SECTIONS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => select(s.key)}
                aria-current={active === s.key ? "true" : undefined}
                data-pressed={active === s.key ? "" : undefined}
                className={cn(
                  "relative z-[1] h-[30px] rounded-ctl px-2.5 text-left text-[13px] font-medium outline-none transition-colors duration-(--d-fast) focus-visible:shadow-focus",
                  active === s.key
                    ? "text-foreground"
                    : s.key === "danger"
                      ? "text-destructive/80 hover:text-destructive"
                      : "text-muted-foreground hover:text-foreground",
                )}
                data-testid={`settings-section-${s.key}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </nav>
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="relative min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden"
          data-testid="settings-panel"
        >
          <div className="flex flex-col gap-7 pb-8 pl-2 pr-5 pt-1">
            <section ref={reg("general")} id="settings-general">
              <GeneralSection form={general} />
            </section>
            <section ref={reg("environments")} id="settings-environments">
              <EnvironmentsSection open />
            </section>
            <section ref={reg("egress")} id="settings-egress">
              <EgressSection open />
            </section>
            <section ref={reg("keys")} id="settings-keys">
              <ApiKeysSection />
            </section>
            <section ref={reg("developer")} id="settings-developer">
              <DeveloperSection />
            </section>
            <section
              ref={reg("danger")}
              id="settings-danger"
              className="min-h-[40%]"
            >
              <DangerSection />
            </section>
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2 px-5 pb-4 pt-3">
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          {general.dirty
            ? "Unsaved changes in General"
            : "Other sections save on their own"}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            if (general.dirty) general.reset();
            else onClose();
          }}
          data-testid="settings-cancel"
        >
          {general.dirty ? "Cancel" : "Close"}
        </Button>
        {general.dirty && (
          <Button
            size="sm"
            disabled={!general.canSave}
            onClick={() => void general.save()}
            data-testid="settings-save"
          >
            {general.saveLabel}
          </Button>
        )}
      </div>
    </>
  );
}
