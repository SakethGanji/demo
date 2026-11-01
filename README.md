# Motion and nteract components

Two sets of React 18 components for the notebook, themed by the same `--ne-*` tokens (light and dark):

- **Motion**: 16 animated scenes. React and plain CSS only.
- **nteract**: every item of the [nteract-elements](https://nteract-elements.vercel.app) registry (cells, outputs, CodeMirror editor, Jupyter widgets, the isolated output iframe), re-themed to the tokens. Built on Tailwind 4, Radix, CodeMirror and friends (see `package.json`).

```
npm install
npm run dev      # http://localhost:5190  →  #/motion and #/nteract
npm run build    # one self-contained dist/index.html, opens straight from disk
```

## Files

| Path | What it is |
| --- | --- |
| `src/tokens/tokens.css`, `src/tokens/modern-tokens.css` | The `--ne-*` colour, surface and shadow tokens, light and dark. Both sets read them |
| `src/motion/Motion.tsx`, `motion.css` | The `Motion` page, the ten basic scenes and the shared helpers (`Scene`, `useProgress`, `Check`, `Spinner`, `Dots`, `Reduced`) |
| `src/motion/Scenarios.tsx`, `scenarios.css` | Six product scenes: an upload that drops and resumes, Ask building cells, a wait ended by an approval, a workflow run, taking over the browser, comparing versions |
| `src/components/` | The nteract components: `cell/`, `outputs/`, `editor/`, `widgets/` (56 widget controls, ipycanvas, anywidget), `isolated/`, `icons/`, `ui/` (shadcn primitives) |
| `src/lib/` | Helpers the components import (`cn`, error boundaries, dark mode, text highlight, `crypto.randomUUID` polyfill) |
| `src/isolated-renderer/` | The bundle that runs inside the output iframe. `npm run build:renderer` builds it into `public/isolated/` |
| `src/nteract.css` | Tailwind (theme + utilities, no preflight) and the mapping from shadcn variables (`--background`, `--primary`, …) to the `--ne-*` tokens |
| `src/nteract/NteractProvider.tsx` | Wrap the app in this: the isolated renderer plus the widget store |
| `src/nteract/NteractGallery.tsx` and the rest of `src/nteract/` | The `#/nteract` gallery: every component running, a pretend kernel, sample media, a coverage counter |
| `src/App.tsx`, `src/main.tsx`, `src/app.css` | The two-page shell |
| `components.json` | shadcn config with the `@nteract` registry, for `npx shadcn add @nteract/<item>` |

## Use

Import `nteract.css` before the tokens (the tokens re-point its ANSI colours), and put content inside an element with class `ne-root`:

```tsx
import './nteract.css'
import './tokens/tokens.css'
import './tokens/modern-tokens.css'
import { Motion } from './motion/Motion'
import { NteractProvider } from './nteract/NteractProvider'
import { OutputArea } from './components/cell/OutputArea'

<div className="ne-root">
  <Motion />
  <NteractProvider>
    <OutputArea outputs={[{ output_type: 'stream', name: 'stdout', text: 'hello\n' }]} isolated="auto" />
  </NteractProvider>
</div>
```

The theme follows `prefers-color-scheme`. Set `data-theme="light"` or `data-theme="dark"` on `<html>` to force one.

**Widgets.** Create the model in the store, then output a widget view:

```ts
useWidgetStore()!.store.createModel('w1', { _model_name: 'IntSliderModel', _model_module: '@jupyter-widgets/controls', value: 4, min: 1, max: 8 })
// output: { output_type: 'display_data', data: { 'application/vnd.jupyter.widget-view+json': { model_id: 'w1' } } }
```

**Markdown, HTML and SVG** never render in the main page. They need `OutputArea isolated="auto"` (or `IsolatedFrame`).

**Motion scenes.** Each scene is a list of timed steps that loops while its card is on screen, using `Scene`. To use one animation on its own, copy its stage component (for example `UploadStage` or `ResumeStage`) and its block from the CSS. The classes are prefixed `mo-` (basic) and `sc-` (product). They sit inside an element with class `mo`, which defines the motion tokens.

## Notes on the nteract code

- `npm run build` runs `build:renderer` first, because `NteractProvider` inlines `public/isolated/*` as text (`?raw`). That folder is build output and is not committed.
- After any `npx shadcn add`, the CLI writes `import { cn } from "cn"` and installs a stray `cn` package. Run `sed -i 's#from "cn"#from "@/lib/utils"#' src/components/ui/*.tsx && npm uninstall cn`, and delete the `* {}` / `body {}` rules it adds to `@layer base`.
- Local fixes to the registry code: `isolated-renderer/index.tsx` imported `@/components/controls` (should be `widgets/controls`). `ImageOutput` treated base64 JPEG (`/9j/…`) as a URL path. `crypto.randomUUID` is polyfilled because the iframe is not a secure context.

## Rules the motion scenes follow

- Durations stay under 300ms, except where the motion shows progress.
- Things arriving ease out. Things landing use the `--mo-spring` curve, which overshoots by under 2%.
- Only transform, opacity and filter animate. Panels open with a `grid-template-rows` transition.
- Entrances start at 95% scale and a few pixels away, never from nothing.
- Progress bars follow real bytes and rows, not a timer.
- Loops pause when off screen.
- Reduced motion (the system setting or the page switch) turns movement into plain fades and stops loops.
