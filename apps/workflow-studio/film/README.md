# Landing film

The source of the "One Engine, Many Uses" film that plays on the studio landing page (`/`). The page
(`src/features/landing/LandingPage.tsx`) loads the built film, `public/landing-film.html`, in a frame and
drives it through `window.film` (`play`, `rest`, `quiet`, `theme`, `pointer`): one scroll step or key press
(→ ↓ Space PageDown, back with ← ↑ PageUp) plays one scene, which then rests on its last frame.

`public/landing-film.html` is generated. Edit the files here, then rebuild.

## Files

| File | What it is |
|---|---|
| `STORY.md` | The words of every scene. Keep the wording when changing visuals. |
| `FRAMEWORK.md` | The design rules: tokens for both themes, the glass components, 3D and motion rules, the "never" list. Read it before changing anything. |
| `kit.css`, `kit.js` | Shared glass components and the film runtime (timeline, `window.film`, theme, pointer lean, transitions, reduced motion). |
| `shell.html` | The page skeleton and background. |
| `slides/00-opening.js` … `08-close.js` | One scene per file. Each registers itself with `Film.slide({...})` and carries its own scoped CSS. |
| `build.py` | Assembles everything into `out/landing-film.html` (one self-contained file). |
| `preview.html` | Previews one slide: `preview.html?slide=N&theme=dark\|light&t=rest`. |
| `tools/*.mjs` | Playwright checks and the walkthrough recorder (they borrow Playwright from `apps/analytics-service/web/node_modules`). |

## Build and ship

```sh
cd apps/workflow-studio/film
python3 build.py --strict        # → out/landing-film.html
python3 build.py --strict --ship # also copies it to ../public/landing-film.html
```

## Check

```sh
python3 -m http.server 5191      # from this folder, for the tools below
node tools/int-qa.mjs            # plays all 9 scenes in the built film, reports errors, clipping, fps
node tools/landing-walk.mjs      # checks the live landing page (studio dev server on :5174) and records a walkthrough
```

The gears and glass need a real GPU. Headless Chromium renders them slowly, so run checks headed
(`--ignore-gpu-blocklist`) when judging motion or fps.

## Design history

The look is the old landing at commit c6f429f (its 3D floating glass hero with the workflow and AI builder
mocks), grown into one glass window per scene, on that landing's calm sky without ribbons or stars. Spectacle
lives in the transitions (glass flying forward, glass gears); resting frames stay clean. The Opening is the
part the owner likes most: change it only with care.
