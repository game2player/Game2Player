# Workflow

## Select the real build

Inspect the live playable iframe, redirects, and source repository. Determine
whether the build is plain JS/canvas, a framework export, WebAssembly, PICO-8, or
an unidentified engine before capture. If Unity, SWF, or the PICO-8 player is
present, use its dedicated skill instead.

Capture the browser's actual request graph into the Builder cache. Include dynamic
imports, workers, WASM, audio, fonts, level files, blob-backed assets, and requests
made only after pressing Start. Account for service workers and browser cache: a
warm online success is not an offline package.

Preserve response MIME types, compression semantics, URL resolution, and query
variants. Use `tools/game_import/pack_game_bundle.py` when payload size requires
the repository's stored-zip shard convention; do not invent another packaging
format.

If a downloaded Electron build stores its web files in `app.asar`, the existing
extractor can unpack a directory without extra dependencies:

```bash
python -m tools.game_import.extract_asar /path/to/app.asar . .cache/tmp/asar
```

Replace `.` with an archive subdirectory to extract only that part. Inspect the
HTML and JavaScript before treating it as a browser build: extracting files does
not remove dependencies on Electron or Node APIs.

## Find state

Inspect readable source, source maps, global/module objects, event buses, save
formats, DOM overlays, and storage. Choose the smallest stable interception point.
Useful state commonly includes scene/mode, level/wave, objective counters, score,
health/resources, player/goal coordinates, and win/loss.

Do not make future agents parse an enormous raw engine object. Normalize only the
fields needed for understandable progress and task verification. Prove each field
with a controlled game event. Keep hidden verifier truth separate from actor-visible
observations when the distinction matters.

## Reset and tasks

Prefer native level select, documented query parameters, seeded startup, or a
small reviewed save snapshot. Clear only game-owned storage on reset. Task variants
must verify that the requested level/character/mode actually loaded.

## Evaluate

Block every external request in a cold browser profile and reach the title/start
screen. Then exercise input, progress, and reset while recording before/after state.
Evaluate each task with the Builder's common task-contract rules; do not require a
normal-play terminal transition when its terminal contract is honestly pending.
Check focus and canvas scaling at the catalog resolution. A page shell, loading
animation, constant state object, or successful network-warm run is not acceptance
evidence. Use one browser at a time and terminate it.
