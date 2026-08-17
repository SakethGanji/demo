/**
 * A scope picker for the cockpit chrome — version, sheet, acting seat.
 *
 * These three sit in the top band of the screen, and they were native
 * `<select>` elements. Nothing breaks the feel of a custom instrument faster
 * than an OS widget in the middle of its chrome: it brings its own font, its
 * own arrow, its own focus ring and its own popup, none of which the design
 * system controls. The prototypes draw them as recessed wells with a mono
 * value and a small chevron, which is what this is.
 *
 * Deliberately NOT applied to the form selects inside panels and dialogs
 * (rule editors, pivot shelves, filter conditions). Those are lower in the
 * visual hierarchy, a native select is a perfectly good form control, and
 * swapping them would rewrite ~56 browser-test call sites for a fraction of
 * the visual return. The line is: chrome gets the custom control, forms keep
 * the native one.
 *
 * ACCESSIBILITY / TEST NOTE: this is a listbox, not a `<select>`, so
 * `selectOption()` does not drive it. Tests click the trigger and then the
 * option — `data-testid` is on both, and `aria-label` is preserved so the
 * control is still findable by label.
 */

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/shared/components/ui/select';
import { cn } from '@/shared/lib/utils';

export interface ScopeOption {
  value: string;
  label: string;
  /** Recessive detail shown after the label, e.g. a row count. */
  hint?: string;
}

export function ScopePicker({
  value,
  onValueChange,
  options,
  label,
  testid,
  className,
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: ScopeOption[];
  /** Accessible name — kept so `getByLabel` still finds it. */
  label: string;
  testid?: string;
  className?: string;
}) {
  return (
    <Select value={value} onValueChange={(v) => onValueChange(String(v))}>
      <SelectTrigger
        aria-label={label}
        data-testid={testid}
        className={cn(
          // A recessed well, matching INSTRUMENT's material treatment for
          // anything you put something into.
          'h-7 gap-2 rounded-md border-0 bg-[var(--s0)] px-2 font-mono text-micro text-foreground shadow-[inset_0_1px_2px_rgba(0,0,0,.35)] ring-1 ring-border focus-visible:ring-2 focus-visible:ring-ring',
          className,
        )}
      >
        {/* Render the LABEL, not the value. Base UI's `SelectValue` shows the
          * raw value unless Root is given a label map, so a version picker read
          * "2" where the rest of the product says "v2". */}
        {options.find((o) => o.value === value)?.label ?? value}
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem
            key={o.value}
            value={o.value}
            data-testid={testid ? `${testid}-option` : undefined}
            className="font-mono text-micro"
          >
            {o.label}
            {o.hint ? (
              <span className="ml-1.5 text-footnote text-muted-foreground">{o.hint}</span>
            ) : null}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
