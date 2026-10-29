---
name: defold
description: >-
  Instrument a Defold HTML5/Emscripten export as a game environment. Use only
  after Builder has identified Defold; do not use for other WebAssembly engines.
---

# Defold Builder

Use ordinary file reads and `rg` for JavaScript. `python -m tools.engine_inspect
inspect games/benchmark/<game_id>` can locate files inside ZIP shards; extract needed
inputs into the run cache. See the [engine tools](../../../tools/engine_inspect/README.md).

Identify the engine from `dmloader.js`, a `*_wasm.js` / `*.wasm` pair, and an
`archive_files.json` beside `index.html`.

Lua executes inside the WebAssembly engine, but scripts are stored in resource
archives as source or bytecode. Static script recovery is distinct from reading
live Lua tables from JavaScript. There is no configured general Defold decompiler;
arcdEx is a version-dependent candidate. Read [workflow](references/workflow.md)
for resource analysis and the existing persisted-state route.

No prebuilt game adapters are included. Use the workflow to inspect this build's
resource formats and persisted state before selecting an instrumentation path.
