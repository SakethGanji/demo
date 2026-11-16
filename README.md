# Project ENSEMBLE · UI

A fresh React 18 + Vite build of the Project ENSEMBLE interface, built on the ENSEMBLE design system: dark shell, light canvas, 1px borders, monospace metrics, and the Harmonic Thread (strings for connection and progress, waves for live computation).

```
npm install
npm run dev      # http://localhost:5190
npm run build
```

## Screens (hash routes)

| Route | What it shows |
| --- | --- |
| `#/workflow` | Quick Train: drop a CSV (profiled in the browser) or pick a sample, choose a recipe (TF-IDF → LogReg (OvR), XGBoost, LinearSVC), and watch a simulated run: the pipeline as one string, one ringing string per one-vs-rest classifier or a damped residual wave per boosting round, fold strings, loss, log, then results |
| `#/gym` | Training Gym: live Fit Estimate and Validation Curve that follow the sliders, a console dock (Validation checks, Dry Run), 10-step tension stepper, Guided / Advanced hyperparameters with fret-track sliders and exact numeric inputs, the objective as a Formula with its where-list, a catenary pipeline on graph paper, a live-diffed run config, label distribution, sticky footer |
| `#/home` | The Workbench: the Project ENSEMBLE lockup over an interactive string field, the Toolkit (eight tools you can pin to the sidebar toolbelt), runs drawn as strings, a live Training Monitor plot, a streaming log, compute utilization heatmap, activity feed, lineage graph, recent runs |
| `#/experiments` | Parallel coordinates of runs, runs table with selection and an `F1 ≥ 0.90` filter, multi-run comparison plot with crosshair hover, parameter diff, confusion matrix on the heat ramp and per-class recall |
| `#/models` | Model Library: cards with each model's equation, port signature and defaults, filter by family |
| `#/preprocessing` | Preprocessing Library: transforms with port contracts, a detail panel, a transform × model compatibility matrix |
| `#/registry` | Model Registry: stage KPIs, version lineage graph, versions table |
| `#/settings` | Customization, kept in the browser: string tension, string motion, density, default Guided / Advanced mode, Vela sidecar, toolbelt |

The shell adds a status bar (compute, live run, current config) and a command palette on ⌘K / Ctrl+K.

Vela, the AI companion, is a collapsible sidecar that gives PatchCards; applying one changes the Training Gym config.

## Files

| Path | What it is |
| --- | --- |
| `src/tokens.css` | Design tokens (colour, type, space, radius, motion) as CSS variables |
| `src/app.css` | Every component style |
| `src/components/ui.tsx` | StatusPill, ParamChip, Segmented, Tabs, ResonanceWave, SparkWave, FretSlider |
| `src/components/charts.tsx` | PlotFrame, LineChart, ConfusionMatrix, ParallelCoords |
| `src/assets/` | The ENSEMBLE string wordmarks and the Vela crest, copied unchanged from the design system |
| `src/components/Strings.tsx` | PluckString, StringField, RunString, ToolGlyph |
| `src/tools.ts`, `src/prefs.tsx` | The tool list and the saved preferences |
| `src/components/Graph.tsx` | Pipeline and lineage graphs on graph paper, scaled to fit |
| `src/components/Vela.tsx` | Vela sidecar and PatchCards |
| `src/components/icons.tsx` | 1.75px line icons and the Vela crest |
| `src/screens/` | Home, TrainingGym, Experiments, Library (Model Library, Preprocessing Library, Registry), Settings |
| `src/data.ts` | Deterministic sample data |
