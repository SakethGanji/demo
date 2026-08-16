# INSTRUMENT — the house visual language (v2)

The thesis: **this is a precision instrument, and precision reads through restraint, not decoration.**
Reference implementations: `terminal-quality-instrument.html` (densest case, v2 amendments) and
`terminal-refined-a.html` (the original cockpit pass). Read one before restyling anything.

## Tokens — copy verbatim into `:root`

```css
--s0:#080A0E;  --s1:#0D1015;  --s2:#11151B;  --s3:#161B22;
--s4:#1C222B;  --s5:#242C37;  --s6:#38424F;  --key:#E9EEF5;
--t1:#E7ECF3;  --t2:#B0BAC8;  --t3:#85909F;  --t4:#5C6675; /* t4 = decoration only, NEVER text */
--r1:rgba(255,255,255,.042); --r2:rgba(255,255,255,.075); --r3:rgba(255,255,255,.13);
--hi:inset 0 1px 0 rgba(255,255,255,.045);
--sig:#46D5E8; --sig-dim:rgba(70,213,232,.14); --sig-line:rgba(70,213,232,.40);
--st-good:#0ca30c; --st-warn:#fab219; --st-serious:#ec835a; --st-crit:#DC5050;
--m1:#CBD3DF; --m2:#A2ACBB; --m3:#7D8797; --m4:#5F6979; --m5:#48515F; --m6:#39414D;
--sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
```

`html{font-size:13px}`; body `12.5px`, `color:var(--t2)`, `line-height:1.4`, `letter-spacing:-0.004em`,
background `radial-gradient(1180px 560px at 44% -12%, #151B24 0%, rgba(21,27,36,0) 62%), var(--s1)`.
Body carries the grain overlay (`body::after`, fractal-noise SVG data-URI, `opacity:.055`,
`mix-blend-mode:overlay`, `pointer-events:none`, `z-index:200`). Focus ring:
`box-shadow:0 0 0 1px var(--s0), 0 0 0 3px rgba(70,213,232,.45)`. Copy the scrollbar rules too.

## The eight rules

1. **The accent is a MEANING, not a count.** Cyan `--sig` marks **scope and liveness** — *"what am I looking
   at, and is it current?"* In practice: the active route, the selected/as-of object, the current
   sheet/scope, and the LIVE lamp. Typically **4 uses per screen regardless of density**. Repeated state
   (11 toggles, a second lamp) must NEVER take the accent — it becomes a texture and stops signalling.
   Repeated active state goes to value + position + elevation instead.
2. **The primary action is near-white (`--key`), not the accent.** It wins on value, which preserves the
   accent budget.
3. **Delete ~90% of borders.** Group by elevation (`--s2`→`--s3`→`--s4`) and by space. A rule returns ONLY
   in three cases: a **horizontal zone boundary** (a dark seam reads as "content ran out"), a
   **heterogeneous table** (mixed column kinds turn to soup — one vertical rule splits definition from
   outcome), and **stacked blocks whose prose wraps** into the next. Three weights only: `--r1/--r2/--r3`.
4. **Numbers are the hero.** Large figures (20–27px, weight 600, `letter-spacing:-0.03em`) over recessive
   9.5px eyebrows. `font-variant-numeric:tabular-nums` on every figure.
5. **Sentence-case headings.** No `UPPERCASE 11PX MICRO-LABELS` — that habit is the loudest generated-looking
   tell. Table headers become **lowercase mono**: they are identifiers, not shouting.
6. **Status = shape + hue + word**, never hue alone. Five states, five shapes, pure CSS:
   good = circle, warning = triangle, attention/serious = rotated square (diamond), critical = square,
   unknown = ring. Always beside a word.
7. **Severity is typographic, not colored.** `error` = `--t1`/600, `warning` = `--t3`. Severity is a
   *property*; coloring it red has it impersonating *status*. This frees the reserved palette entirely.
8. **Cards earn a SURFACE, not a border** — and only for heterogeneous, independently actionable, ranked
   units. **Never nest a container inside a container**; a guard box inside a card becomes a left-ruled
   indent. Elevation contrast replaces the second border.

## Material

SVG fractal-noise grain over everything; a backlit radial vignette on the body; `--hi` inset top highlight
on raised edges; recessed wells (`inset 0 1px 2px rgba(0,0,0,.6)`) for inputs and for masked PII;
`box-shadow` for elevation, never a border where elevation will do.

## Color discipline

- **Categorical viz only where identity genuinely needs hue.** Prefer direct labels + a neutral value ramp
  (`--m1`…`--m6`) for magnitude. Most single-series charts need no categorical color at all.
- Categorical set, fixed order, never cycled: `#3987e5 #d95926 #199e70 #c98500 #d55181 #747c04 #9085e9 #e66767`.
  Above 8 categories: rank, take top 8, fold the tail into a **graphite "Other"** with its own count/share.
- Status colors are **reserved** and never a data series.
- Text always wears ink tokens (`--t1/--t2/--t3`), never a series hue.

## Rest and hierarchy

When there is spare room, use it — one region breathes so another can be dense. When there is not,
hierarchy is bought with **contrast range**: one footnote register (9.5px `--t3`) absorbs *all* provenance
(`evidence …`, run ids, artifact paths, cap labels), which frees `--t1` for ~12 anchors on the screen.
Establish a clear first-read / second-read / third-read.

## Non-negotiable

- Body text **≥4.5:1 measured** against its composited surface (`--t4` is decoration only, never text).
- Status never rides on hue alone. Interactive targets ≥24px.
- System fonts only. **ZERO network resources.** Renders without JS.
- Fixed 1440×1024, panes scroll internally, **no page scroll**.
- **A restyle is not a redesign.** Same information, same regions, same layout, same content.
