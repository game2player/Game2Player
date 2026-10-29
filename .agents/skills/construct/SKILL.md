---
name: construct
description: >-
  Instrument a Construct 2 or Construct 3 HTML5 export as a game
  environment. Use only after Builder has identified Construct; do not use for
  other HTML5 engines.
---

# Construct Builder

Use ordinary file reads and `rg` for JavaScript. `python -m tools.engine_inspect
inspect games/benchmark/<game_id>` can locate files inside ZIP shards; extract needed
inputs into the run cache. See the [engine tools](../../../tools/engine_inspect/README.md).
Confirm C2 versus C3 and main-thread versus worker execution before choosing an accessor.

Identify the engine from `c2runtime.js` or `c3runtime.js` beside `index.html`, and
from a `<canvas id="c2canvas">` or `c3canvas` in the markup.

Construct 2 commonly exposes its main-thread runtime through `cr_getC2Runtime()`.
Construct 3 uses a different runtime and can run in a worker; a main-page global
is not guaranteed. Do not claim C2's accessor works for C3. Read [workflow](references/workflow.md) for the object graph and the
fields worth reading, then keep `game_api.js` to a thin projection of it.

Inspect this build's runtime before implementing time, pause, layout, and entity
enumeration. No prebuilt game adapters are included; use the engine workflow as
the reference and verify each accessor against the captured build.
