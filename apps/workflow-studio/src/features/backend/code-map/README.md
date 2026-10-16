# Backend code map

The backend editor offers Visual map, Visual + code, and Code. Code stays the
initial view for existing editor workflows. The map consumes `doc.source`, including
unsaved manual changes and completed AI drafts; it does not write to the document,
module, routes, releases, secrets, or runtime.

`POST /api/projects/{project}/modules/code-map` accepts `{source, module_id?}`.
It uses the existing project lookup and, when supplied, checks that the module
belongs to that project. This inherits the engine's existing access model; it does
not add authentication. New unnamed drafts need no module id. It streams a bounded
request, limits UTF-8 source to 200,000 bytes, and parses in a worker thread. Results
have `Cache-Control: no-store` and a SHA-256 of the exact source analyzed. No LLM,
secret lookup, code import, execution, or migration is needed.

The debounced client aborts old requests and rejects obsolete responses. Render-time
source equality marks the old graph stale immediately after an edit. Syntax errors
and network failures keep an explicitly dimmed last valid map with source jumps
disabled. Failed requests can be retried. A project/module change resets the map;
analysis is paused while the generator is streaming.

Functions have a single shared definition. Expanded functions expose source-linked
call sites and their outgoing handles. Recursive components are condensed for
layering. Auto layout uses barycenter sweeps within layers; it reduces clutter but
does not guarantee a crossing-free graph. References that skip layers route through
the module gutter. Graph pan/zoom is intentional, so only the canvas is marked as a
scrolling surface for layout checks.

Hover or keyboard-focus a function to trace its neighborhood. Pins persist that
highlight. Focus selection, direction and one/two hops select related functions;
search and endpoint/connector/unresolved/secret-name filters intersect that scope.
The status line counts hidden functions. Open exact source switches to split view
and selects the definition line in the existing CodeMirror editor. Call sites and
control-block locations also jump to source.

## Scope and limits

This is a conservative single-module syntax projection, not an execution trace or
an editable workflow graph. It resolves unshadowed direct local calls and recognized
Studio SDK connector syntax; effects of connectors and database queries are not
inferred. Secret reference **names** are exposed, never resolved credential values.
Nested functions, classes, lambdas, external methods, dynamic dispatch and imports
across modules are not fully mapped. Control-block kinds/locations are shown, not
business interpretations of their conditions. Duplicate function definitions,
modules above 200 functions, and trees above 20,000 syntax nodes produce an explicit
unsupported/invalid view instead of a partial graph claiming synchronization.

## Validation

- Engine: `python -m pytest tests/unit/test_code_map.py tests/integration/test_code_map.py`
- Studio: `npm run test:unit`, `npx tsc -b`, `npx vite build`
- Browser: `npx playwright test -c e2e 10-code-map.spec.ts`

Browser tests use the real isolated engine and database, including the 22-function
risk example. They exercise read-only draft analysis, filters, source navigation,
obsolete responses, stale syntax, project/module boundaries, recoverable service
failure, empty/unnamed modules, and theme screenshots. Only failure injection and
response delays are intercepted; source analysis is real. Screenshots are written
to `screenshots/ui-built/backend-code-map-{light,dark}.png`.
