# Film 2 — design framework

A scroll-driven film for a 7-minute talk, built from scratch in the **c6f429f / revert-to-5684ba5 landing language**.
Nine scenes, one file each, one agent per slide. This document is the contract. Read it fully before writing a line.

Folder: `apps/workflow-studio/film/`
(`FILM2` below). Story text: `FILM2/STORY.md` — **its wording is exact; never paraphrase a label, number or caption.**
Worked example: `FILM2/slides/00-opening.js` — copy its structure.

---

## 1. What made c6f429f great (and what we keep)

The owner's words: *"simple colour, but you can see the animations and the 3D elements … look how good the workflow builder and the AI builder look … the glass-like look."*

1. **One hero object per scene.** c6f429f's landing had a single 3D glass window (the AI builder) under a big headline. Everything lived *inside* that window: a dotted canvas well, nodes, a side panel with a stepper. There was nothing floating around it. **Each slide here = one hero window (or one tight cluster that reads as one object), built in that same window language.**
2. **The window is real UI, not an illustration.** Title bar with three dots and a title, a right slot with a chip, a recessed dotted canvas, raised cards, steppers, buttons that say Keep / Discard. Density like a real product screenshot, legibility like a slide.
3. **3D is in the object, not the background.** The window tilts in from rotateX 26deg and settles; it leans 2–3deg toward the pointer; a conic halo rim slowly circles it; a soft sheen follows the pointer. The background is a still sky with three diagonal beams.
4. **Motion tells the product story.** Things *build*: a dashed blueprint node becomes solid with a pop, edges draw once both ends exist, the stepper ticks Ask → Plan → Build → Review, numbers count, text types. Never decorative motion.
5. **Simple colour.** Sky-blue/navy brand gradient, one accent (AI violet), green for ok, gold for warn, red for bad. Lots of white/glass. Nothing else.

What we **remove** from what exists today: the spiral/aurora background, particles, stars, ribbons, sketches, scattered chips/notes, ember/orange, teal, the top bar. Do not look at the current film (see §11).

---

## 2. Stage, layers, file contract

* The stage is **1600 × 900 stage px**, centred and scaled to fit the viewport (`--k-scale`). Lay out in stage px with absolute positioning or CSS grid. It must read at 1280×720 and 1920×1080 (it is the same stage, scaled — but text below 11px will be unreadable at 720p: **min 11px, body 12.5–14px**).
* Layers (kit-owned): `#k-bg` (sky + 3 beams + vignette, still) → `#k-scenes` (one `section.k-scene.s-<id>` per scene) → `#k-gears` (transition-only glass gears).
* Each scene: `section.k-scene.s-<id> > .k-approach (kit transition transform) > .k-content > [header.k-head] + .k-area`.
* **`.k-area`** = your build region: `left 64, right 64, top 214, bottom 56` (1472 × 630 stage px), `perspective: 1600px`. With `head:false` you get the whole 1600×900 content root as `root` (3rd arg of `build`).
* **Keep clear:** top-right **140 × 64** (theme toggle), the right **72px** strip (scroll rail at right-4), and on the Opening only the bottom-centre **240 × 60** ("Scroll" hint). Nothing important may sit there; glass edges may pass under the rail.

### File contract

One file per slide: `FILM2/slides/NN-name.js` (`01-engine.js` … `08-close.js`). It calls exactly once:

```js
Film.slide({
  index: 1, id: 'engine', name: 'Engine', word: 'The engine',   // index = story order 0..8; id = css scope
  kicker: '01 · Where it started · a workflow engine, built from scratch', // ' · ' splits; first part bold
  caption: 'Every workflow is a graph you can read.',            // or captions: [{at, text}, ...]
  sub: '…', subAt: 2.0,                                          // optional muted line under the title
  headAt: 0.45,                                                  // kicker time (caption at headAt+0.12)
  head: true, headAlign: 'left' | 'center',                      // head:false = no header, full root
  dur: 10,                                                       // seconds the scene plays, then rests
  css: `.s-engine .foo { … }`,                                   // ALL selectors scoped under .s-<id>
  build(area, kit, root) { …; return state },                    // create DOM once
  timeline(tl, kit, state) { … },                                // register tweens on tl
  at(t, kit, state) { … },                                       // optional per-frame hook (rare)
})
```

Rules:
* **CSS scoped under `.s-<id>`**, colours only via tokens (`var(--primary)` etc). No hex except in gradients copied from kit tokens' intent. No `!important`. Do not restyle `.k-*` globally.
* **No DOM measuring** (`getBoundingClientRect`, `offsetWidth`) — the stage is scaled; compute positions from your own numbers. (`pointAt` on SVG paths is fine.)
* **An element you tween with transform props (x, y, z, s, rx…) must not carry its own CSS `transform`.** Wrap it, or tween a child. E.g. `.k-node` has `transform: translate(-50%,-18px)` → tween `node.querySelector('.t')` for scale, tween the node itself only for `opacity`.
* No `setTimeout`/`setInterval`/`requestAnimationFrame`, no CSS `animation`/`transition` for story motion — everything must be a pure function of t via `tl` so `rest()` and seeking are exact. (Kit loops — halo spin, caret blink, shimmer — are the only CSS animations, and they stop under reduced motion.)
* The source must not contain the string `</script` (build.py rejects it).
* No external assets, no images, no fonts other than Inter (+ the kit mono stack), no WebGL/canvas. CSS 3D + DOM glass only.
* No console errors, no exceptions.

---

## 3. Tokens (kit.css — use these, never invent colours)

Light is `:root`, dark is `:root[data-theme="dark"]`. Both are already tuned; just use the names.

| group | tokens | notes |
|---|---|---|
| ground | `--bg --fg --muted --faint --hairline --input --recess` | light bg #eef2fa, dark #050b10 |
| brand | `--brand-1 --brand-2 --primary --primary-fg --brand-soft` | light navy #0b3a9e → azure #2f8fe0, primary #1d5bd0; dark #3d8bff → indigo #8b7dff |
| AI | `--ai --ai-text --ai-soft` | violet; only for "AI did this" |
| status | `--ok(-text/-soft) --warn(-text/-soft) --bad(-text/-soft)` | warn is **gold**, never orange |
| node kinds | `--k-trigger --k-code --k-data --k-flow --k-person --k-out` | pass `kind:` to `node()/ncard()` |
| charts | `--viz-1 … --viz-5` | blues/indigos/violet/green/gold |
| code | `--syn-k --syn-s --syn-f --syn-n --syn-c --syn-d` | via `.k-code` token classes |
| glass | `--glass-fill --glass-elev --glass-rim --hero-depth --sheen` | the window material (`--glass-fill` stays a plain colour: safe in `color-mix`/`background-color`) |
| glass finish | `--glass-tint --glass-spec --glass-edge --grain --rim-spec --glass-blur` | layered parts of the liquid-glass finish (§4 Glass finish) |
| small parts | `--face --drop --hi-line` | convex face gradient, contact+ambient shadow, inner top highlight colour |
| inner | `--well --well-elev --raised --raised-hi --raised-ring --node-bg --node-ring --grid-dot --edge --mask-fill` | |
| radii | `--r-panel (window) --r-well --r-ctl --r-chip --r-kbd` | |
| type | `--font` (Inter) `--mono` | |
| pointer | `--lx --ly` (−1..1, spring-smoothed) | drive `.k-lean`, `.k-sheen` |

**Forbidden colours:** any orange/ember/amber-red, any teal/cyan glows, rainbow gradients. Dark-mode glows are royal blue or indigo.

---

## 4. Components (kit.css classes + kit.js builders)

### Hero window (the object of every scene)
`kit.window({ title, right, w, h, halo=true, bar=true, bodyClass })` → `{ el, hero, win, bar, title, right, body }`

```
.k-lean            pointer lean (rotateX ly·-2.2deg, rotateY lx·2.8deg)  ← do not tween this element
 └ .k-hero         isolation wrapper
    ├ .k-halo>span  conic brand rim, spins 9s (ONE halo per scene: the hero)
    └ .k-win        clear glass, rim gradient, inset top highlight, floating shadow, overflow hidden
       ├ .k-bar     3 dots · .k-wtitle · .k-wright (chips go here)
       ├ .k-body    12px padding; your content
       └ .k-sheen   pointer light
```
Put the window inside **your own tilt wrapper** (`transform-style: preserve-3d`, `transform-origin: 50% 0%`) and tween the wrapper (see Opening `.op-tilt`). Pass `h:` for a fixed-height window (body becomes flex:1).
* `.k-glass` — same material, no bar/halo: for a *second* glass object (a detached card that flies out of the hero, a stat plate). Use sparingly: max 1–3 per scene, and they must belong to the hero story.

### Glass finish (kit-owned, "liquid glass")
Every kit glass surface is built from the same stack. Use the classes and you get it for free; when you hand-roll a
glass surface in your slide's css, compose the same tokens instead of a flat fill.

| layer | how | where |
|---|---|---|
| fill | `--glass-fill` (plain colour) | `.k-win .k-glass .k-kicker .k-chip.glass` |
| inner tint | `--glass-tint`: a faint 158deg gradient, brighter top-left, cool (royal/indigo) bottom-right | same |
| edge brightening | `--glass-edge`: radial gradient, clear centre, edges lift (refraction-like) — a background layer, not an inset blur shadow (cost) | `.k-win .k-glass` |
| grain | `--grain` at `0 0 / 180px 180px`: a 4–5% inline SVG noise tile that kills banding on large gradients | `.k-win .k-glass` |
| specular line | `--glass-spec` at `50% 0 / 100% 1px no-repeat`: 1px top highlight that fades out before the corners | windows, glass cards, kicker |
| rim | `::after` 1px mask ring = `var(--rim-spec), var(--glass-rim)`: brightest top-left, fading round, a cool glint bottom-right; `--rim-spec` is a rim light that slides with the pointer (`--lx --ly`) | `.k-win .k-glass .k-kicker` |
| frost | `backdrop-filter: var(--glass-blur)` (blur 20 + saturate) — **only** `.k-glass` and `.k-kicker`, never the hero `.k-win` (over the still sky it buys nothing and costs ~5 fps) | detached glass |
| shadow | `--glass-elev` (inner highlights + 1px contact) + `--hero-depth` (contact, near, one wide ambient tinted navy) | windows, glass cards |

Inside the glass, small parts are **convex**: `background: var(--face), <colour>`, a `var(--hi-line)` inset top line, a .5px
ring, and `var(--drop)` (1px contact + a short ambient). Wells are **carved**: an inner top shadow plus a 1px light lip
below (`--well-elev`). Buttons are glossy (white-to-clear top half over the brand gradient, inner top highlight, dark
bottom lip). Icon tiles, ncard tiles, tokens, chips and stepper marks carry a .5px ring in their own colour and a top highlight.

Rules for slides: keep text at rest flat and unfiltered (no `filter`/`blur` on text, no `backdrop-filter` on many small
elements); blur only things that are *behind* (depth of field), never content the eye should read; keep big-radius
box-shadows to one per surface (blur radius is what costs frames); radii stay `--r-panel / --r-well / --r-ctl`.

### Inner layer (inside windows)
* `.k-well` recessed panel (use for canvases, lists). `.k-dots` adds the 16px dot grid (canvas).
* `.k-raised` raised card on the well; `--raised-tint` recolours its ring (e.g. `style --raised-tint: color-mix(in srgb, var(--ai) 40%, transparent)`).
* `.k-recess` small sunken pill area; `.k-hr` hairline; `.k-label` 11px uppercase faint; `.k-meta` 12px muted; `.k-mono`.

### Canvas
* `kit.node({ x, y, name, meta, icon, kind, lg })` — c6f429f node: 36px tile (46 with `lg`) + label (+ meta). `(x,y)` = tile centre-top in parent px. `.blueprint` class = dashed outline state (toggle with `tl.step`).
* `kit.ncard({ x, y, name, meta, icon, kind, w })` — wide node card (icon tile + name + meta in a row, 52px tall), top-left at (x,y). Use for readable graphs with config text ("Weekdays · 07:00").
* `kit.edges(w, h, [[x1,y1,x2,y2,cls], …])` → `{ svg, paths }`; horizontal-ish bezier. `cls`: `hot` (strong), `dash`. Container must be `position:relative` with the same px size.
* `kit.pointAt(path, p)`, `.k-pulse` (8px glowing dot) + `kit.travel(dot, path, {at, dur})` = a run travelling an edge.

### Panels & controls
* `kit.stepper([{title, meta}])` → `{ el, items, set(i) }` (i done-before, i current; `set(4)` = all done). Update `items[k].querySelector('.mt').textContent` inside `tl.fn`.
* `.k-chip` (+ `.ai .brand .ok .warn .bad`, `.sm`, `.glass`), `.k-dot`.
* `.k-btn` (gradient pill) `.ghost` `.sm`; `.press` = pressed scale (toggle with `tl.cls(el,'press',a,b)`).
* `.k-icon-tile` (`.lg`, colour via `--kc`), `.k-av` (`.b .c .d`) + `.k-avs` stack, `.k-tok` value token (`.ai .ok`), `.k-kbd`.

### Data
* `kit.table({ cols:'80px 1fr 120px', head:[…], rows:[[…]] })` → `{ el, head, rows }`. Row classes `.hl` (selected, brand ring + glow) `.dim`. Cells may be nodes. `.num` for right-aligned mono.
* `kit.mask('4821')` → `🔒 •••• 4821` masked value (IBAN).
* `kit.spark(values, w, h, {area})` → `{ svg, line, area, X, Y }` — tween `line` with `draw`.
* `kit.bars(values0to1, { hl:[i], mut:[i] })` → `{ el, bars }` — tween bars `{ sy:[0,1] }` (origin bottom).
* `.k-stat` (`.v` big number, `.l` label) + `kit.count`. `.k-meter > span` (tween `sx`).
* `kit.code([[[text, cls], …], …], { gutter })` → `{ el, lines }`; cls in `k s f n c d`. `.k-caret` blinking caret.
* `.k-shimmer` skeleton block.

### Icons
`kit.icon(name, size, cls)` — lucide-style set: clock calendar code terminal database filter branch merge repeat layers user userCheck users check checkCircle x xCircle zap webhook form file globe mail send sparkles lock unlock shield shieldCheck key box cpu table chart trend pie play pause search eye eyeOff flask browser cursor hand arrowRight arrowUpRight history refresh notebook link alert plug graph braces workflow compare wand download hash list gauge dot plus minus rows pivot gitCommit tag. Need another? Inline an SVG via `kit.html()` with the same stroke style (`class="k-i"`, 24 viewBox, stroke 2).

### DOM helper
`kit.h('div.a.b', { style:{…}, '--var': v, text, html, attr: v }, ...kids)`; kids can be strings, nodes, arrays, null. `kit.html(str)` → node. `kit.append(el, kids)`.

---

## 5. 3D rules

Depth tiers, back to front:
1. **Background** — still. Never animate it. Never add anything to it.
2. **Scene approach** — kit-owned (transition). Do not touch `.k-approach`.
3. **Hero** — your tilt wrapper: entrance `rx 26→6..8deg, s .9→1, y 40→0` over ~2.6s `soft`, opacity 0→1 over 0.9s `out`, starting 0.3–0.6s. It **rests slightly tilted** (6–8deg rx, optionally ry ±4–8deg for a side-on hero). Plus the pointer lean via `.k-lean` (automatic in `kit.window`).
4. **Inner raised** — cards/nodes inside the window; they can lift in z (`z: [-40, 0]`, with `preserve-3d` on the chain) for a reveal, but at rest they are flat in the window.
5. **Detached glass** (optional, ≤3) — `.k-glass` cards that physically leave the hero (fly out, unfold, stack). This is where a slide earns its "complex 3D component" — e.g. blocks opening like a box lid (rotateX on a hinge), a card peeling forward in z.

* `preserve-3d` must be on every ancestor between the perspective element and the 3D child, or the depth flattens.
* Max tilt at rest: 10deg. Max during motion: 30deg. No spinning objects (only the kit halo/gears rotate).
* `backdrop-filter` only on a few detached glass objects (cost) — the kit frosts `.k-glass` and the kicker, not the hero window. Never on many small ones.

---

## 6. Motion vocabulary

All helpers are bound to your timeline (`kit.x(...)` in `timeline`). Times in seconds from the scene's start.

| helper | use |
|---|---|
| `kit.reveal(els, {at, stagger=.06, dur=.7, y=14, x, s, e})` | default entrance: fade + rise |
| `kit.pop(els, {at, stagger, dur=.55, from=.86})` | a thing landing (badge, node tile, check) |
| `kit.kinetic(el, {at, stagger=.08, dur=.95})` | headline words rising out of masks |
| `kit.type(el, text, {at, cps=38, caret})` → end time | the user's ask, a query being written |
| `kit.count(el, to, {at, dur, from, fmt})` | numbers (use `fmt` for `$12,600.50`) |
| `kit.draw(paths, {at, stagger, dur})` | edges, sparklines, outlines |
| `kit.swap(el, [{at, text}])` | a value/state that changes (`stuck → released`) |
| `kit.travel(dot, path, {at, dur})` | a run moving along an edge |
| `tl.add(el, {prop:[from,to]}, at, dur, ease)` | raw tween; props: `opacity x y z xp yp rx ry rz s sx sy clip clipy draw w h blur --var` |
| `tl.stagger(els, props, at, stagger, dur, ease)` | |
| `tl.fn(at, dur, (p, t) => …, ease)` | per-frame logic (stepper state, counters) — must be idempotent in t |
| `tl.step(at, on => …)` | a switch at a time (class toggle) |
| `tl.cls(el, name, at, until)` | class on during [at, until) |

Eases: `out` (default settle) · `soft` (long hero settle) · `io` (move between two rests) · `in` (exits) · `spring` (small overshoot) · `pop` (bigger overshoot) · `linear`.

Rhythm:
* The kit transition brings your scene in during **0.22–1.5s**. Start your own first reveal at **≥0.5s**; the hero's main action at ~1.0s.
* The header: kicker at `headAt` (0.45), caption at +0.12 (kinetic), sub after. Pace content so the caption is read first.
* One beat at a time: never more than ~2 things moving at once. Stagger 0.05–0.12s.
* **`dur` 7–14s.** The scene rests on its final frame, which must be a complete, beautiful, readable composition (this is what sits on screen while the speaker talks). Ask/Reuse may go to 16s.
* Before a prop's first tween, the element holds the `from` value (so hidden things stay hidden at t=0). After the last, it holds `to`.

---

## 7. Transition (kit-owned — do not reimplement)

Forward: the old scene flies toward the viewer (z 0→760, fade, 0.75s), three clip-path **glass gears** sweep past in the viewport layer (backdrop-blurred, rim-lit, turning), the new scene arrives from depth (z −900→0, rx 16→0, y 50→0, from 0.22s over 1.3s, `soft`). Backward reverses it. Gears exist **only** during transitions. Resting frames are clean. Your scene has nothing to do for this except keep its first ~0.5s calm.

---

## 8. Typography & header

* `.k-head` at left 64 / top 52 (`.center` variant). Kicker = glass pill with gradient dot; first part bold.
* `.k-title` 54px / 720 / −0.042em — the caption. `.k-slam` 84px for Close. `.k-sub` 17px muted. `.k-grad` gradient text (use on 1–2 words max, e.g. "Many uses.").
* In-window text: 11px labels, 12.5–14px body, 26–34px stat numbers. Inter weights 400–800 loaded.
* Never more than one headline per frame. Captions swap in place (`captions` array).

---

## 9. Data rule

Every label, number, name, caption and kicker comes from `STORY.md` **verbatim** (plus the extra detail in your brief, which was derived from it). Realistic supporting values (row ids, timestamps, column names) are fine when the brief allows them; never contradict the story numbers. Never say "n8n clone". No Lorem, no "Acme".

---

## 10. Preview, build, screenshots

* Server: run your own static server on a free port in **5192–5199** from `FILM2`: `python3 -m http.server 519X --bind 127.0.0.1 & echo $! > tools/server-NN.pid`. Kill **by that PID only** when done. Never touch 8000, 8010, 8095, 5173, 5174, 5191.
* Preview a slide alone (no build): `http://127.0.0.1:519X/preview.html?slide=N` — query: `&theme=dark`, `&t=rest` (final frame), `&t=2.4` (freeze at 2.4s), `&from=M` (transition from M; with `&t=` freezes mid-transition), `&still=1`, `&autoplay=0`. Keys: ←/→, r replay, d theme, q quiet.
* Screenshot (prints console errors as JSON): `node tools/shot.mjs "http://127.0.0.1:519X/preview.html?slide=N&t=rest" tools/NN-light.png 1280 720 900` (args: url, out, w, h, waitMs; add `&theme=dark`). **Screenshots are costly: take few** — rest frame light 1280×720 + dark 1920×1080, plus one or two mid-play frames (`&t=`). Read them back to check. Required: `{"errors":[]}`.
* Build the single file: `python3 build.py` → `out/landing-film.html` (missing slides become placeholders). **Only the integrator runs `--ship`.**

---

## 11. Never

* Never hand-edit `public/landing-film.html`: it is generated by `build.py --ship` and the next build overwrites it.
* Never edit studio source (`apps/workflow-studio/src/**`, `index.css`) or `public/landing-film.html`. Never edit `kit.css`, `kit.js`, `shell.html`, `build.py`, `preview.html` or another agent's slide — if the kit lacks something, build it inside your slide's `css`/`build` and mention it in your report.
* Never `git commit`, `push`, `checkout` or `stash`. Never read `.env*` or `blind9db/db-url.txt`.
* No stars, particles, spirals, ribbons, aurora, sketches, scattered chips/notes/sticky labels, floating decorative icons. No ember/orange, no teal. No WebGL/canvas. No top bar. No background animation.
* No motion that isn't telling the product story. No looping motion in your slide (except kit loops).
* No text below 11 stage px. No content in the keep-clear zones.
