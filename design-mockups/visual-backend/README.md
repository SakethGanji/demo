# Visual backend UI concepts

Open `index.html` in a browser. It is self-contained: no server, API, external assets, fonts, or provider keys are required.

Three alternate directions share the banking example:

1. **Application atlas** — application → business operations → connected resources; select a card to inspect its role, inputs, and dependencies.
2. **Operation story** — a readable sequence with input rules, two explicit database reads, custom transformation logic, and a response contract.
3. **Logic workbench** — a canvas for visually managed operations, with draggable steps, an inspector, local label/page-limit edits, and an Add step menu.

All three offer Code, illustrative run playback, light/dark appearance, and responsive layouts. These are product/design concepts with illustrative content, not extracted source graphs or live execution evidence. No runtime/API integration is implemented. The Code view is a shortened example, not the actual banking module.

Recommendation: combine the atlas for navigation with the story for understanding an existing operation. Introduce the workbench when supported visual operations have an authoritative structured definition and deterministic code generation.

## Code-synchronized direction

`code-map/` is a separate working prototype using the real banking Python module and AST
analysis. It replaces illustrative graph structure with source-derived module/function/call
containers, an editable code pane, exact source hashes and explicit stale/error handling.
See `code-map/README.md` for running it and its extraction limits. This is the recommended
next direction for the Command backend view; the three designs above remain interaction studies.
