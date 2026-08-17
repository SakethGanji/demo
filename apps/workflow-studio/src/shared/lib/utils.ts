import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

/**
 * `cn` MUST know about our custom type scale, or it silently deletes colours.
 *
 * tailwind-merge resolves conflicts by putting each class in a group and
 * keeping the last one per group. It ships with Tailwind's own scale, so it
 * knows `text-xs` is a font size and `text-red-500` is a colour. It does NOT
 * know about the scale `@theme inline` adds in `index.css` — `text-micro`,
 * `text-footnote`, `text-body` and the rest — so it guessed, and it guessed
 * COLOUR. The result:
 *
 *     cn('text-muted-foreground', 'text-micro')      → 'text-micro'
 *     cn('text-[var(--st-good)]', 'text-small')      → 'text-small'
 *
 * Every component that sets an ink token and then receives a size through
 * `className` lost its colour. That is most of `instrument/`: `Severity` was
 * rendering `error` and `warning` in the same ink, so INSTRUMENT rule 7 was
 * dead on arrival; `Status` lost the hue on its shape, taking rule 6 with it.
 * Nothing errored — the classes were simply gone.
 *
 * Registering the scale here fixes every call site at once, which is the only
 * sane place to fix it: patching the components individually would leave the
 * hazard armed for the next one.
 *
 * KEEP THIS LIST IN SYNC with the `--text-*` block in `index.css`.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: [
            "footnote",
            "micro",
            "small",
            "body",
            "label",
            "lead",
            "figure",
            "hero",
          ],
        },
      ],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
