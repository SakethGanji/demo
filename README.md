# One Engine, Many Uses: the landing film

This branch holds only the landing page: the scroll-driven film for the 7-minute talk, plus a small page that plays it.
Nothing else from the studio is here.

## Run it

```sh
npm install
npm run dev          # http://localhost:5188
```

Present with the keyboard or a clicker: → ↓ Space PageDown go to the next part, ← ↑ PageUp go back. The top-right
button switches light and dark. The last stop links to the studio, which runs separately; point it somewhere else
with `VITE_STUDIO_URL=http://host:port npm run dev` (default `http://localhost:5174`).

## What is here

| Path | What it is |
|---|---|
| `src/LandingPage.tsx` | The page: a full-screen frame with the film, one snap stop per scene, the chapter rail, the end stop. |
| `src/index.css` | Only the tokens and glass styles the page uses (from the studio's theme). |
| `public/landing-film.html` | The built film: one self-contained file. It also opens on its own in any browser. |
| `film/` | The film's source. Read `film/README.md` before changing it. |

## Change the film

Edit the files in `film/` (`STORY.md` is the wording, `slides/0N-*.js` are the scenes), then rebuild:

```sh
npm run film         # = cd film && python3 build.py --strict --ship → public/landing-film.html
```

Checks and the walkthrough recorder are in `film/tools/` (they use the `playwright` dev dependency; run
`npx playwright install chromium` once). `landing-walk.mjs` and `iq-rm.mjs` expect `npm run dev` on :5188; the rest
expect `python3 -m http.server 5191 --bind ::` run from `film/`.
