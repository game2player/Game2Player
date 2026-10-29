---
name: gamemaker
description: >-
  Instrument a GameMaker Studio HTML5 export as a game environment. Use only
  after Builder has identified GameMaker; do not use for other HTML5 engines.
---

# GameMaker Builder

Use ordinary file reads and `rg` for JavaScript. `python -m tools.engine_inspect
inspect games/benchmark/<game_id>` can locate files inside ZIP shards; extract needed
inputs into the run cache. See the [engine tools](../../../tools/engine_inspect/README.md).
Native GameMaker data-file decompilers do not apply to HTML5 JavaScript.

Identify the engine from `gml_Object_` prefixes in the bundle, an `html5game/`
directory, or a `*.js` whose source contains `gml_Script_`.

The runtime is reachable, but every accessor is a two-letter minified global whose
name changes between exports. Do not copy names from another game: find this
build's own. Read [workflow](references/workflow.md) for how.

No prebuilt game adapters are included. Find this build's own runtime handles
using the engine workflow, and record the resolved accessors in its `game_api.js`.
