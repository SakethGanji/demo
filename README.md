# Motion components

Animation scenes for the notebook: React 18 components and plain CSS, no other dependencies.

## Files

| File | What it is |
| --- | --- |
| `src/motion/Motion.tsx` | The `Motion` page, the ten basic scenes and the shared helpers (`Scene`, `useProgress`, `Check`, `Spinner`, `Dots`, `Reduced`) |
| `src/motion/motion.css` | Motion tokens (`--mo-ease-out`, `--mo-spring`, durations) and the styles for the basic scenes |
| `src/motion/Scenarios.tsx` | Six product scenes: an upload that drops and resumes, Ask building cells, a wait ended by an approval, a workflow run on the page view, taking over the browser, comparing versions |
| `src/motion/scenarios.css` | Styles for those six scenes |
| `src/tokens/tokens.css`, `src/tokens/modern-tokens.css` | The `--ne-*` colour, surface and shadow tokens the CSS reads, light and dark |

## Use

```tsx
import './tokens/tokens.css'
import './tokens/modern-tokens.css'
import { Motion } from './motion/Motion'

export default function App() { return <Motion /> }
```

The theme follows `prefers-color-scheme`. Set `data-theme="light"` or `data-theme="dark"` on `<html>` to force one.

Each scene is a list of timed steps that loops while the card is on screen, using `Scene` from `Motion.tsx`. To use one animation on its own, copy its stage component (for example `UploadStage` or `ResumeStage`) and its block from the CSS. The classes are prefixed `mo-` (basic scenes) and `sc-` (product scenes). All of them sit inside an element with class `mo`, which defines the motion tokens.

## Rules the scenes follow

- Durations stay under 300ms, except where the motion shows progress.
- Things arriving ease out. Things landing use the `--mo-spring` curve, which overshoots by under 2%.
- Only transform, opacity and filter animate. Panels open with a `grid-template-rows` transition.
- Entrances start at 95% scale and a few pixels away, never from nothing.
- Progress bars follow real bytes and rows, not a timer.
- Loops pause when off screen.
- Reduced motion (the system setting or the page switch) turns movement into plain fades and stops loops.
