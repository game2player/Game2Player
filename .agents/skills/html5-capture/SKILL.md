---
name: html5-capture
description: Capture, package, instrument, and validate one browser-native HTML5 game, including itch.io iframe builds, as a self-contained game environment. Use for JavaScript/WebAssembly/canvas/DOM games after ruling out Unity WebGL, Flash, and PICO-8.
---

# HTML5 Capture Builder

Use ordinary file reads and `rg` for JavaScript. `python -m tools.engine_inspect
inspect games/benchmark/<game_id>` can locate files inside ZIP shards; extract needed
inputs into the run cache. See the [engine tools](../../../tools/engine_inspect/README.md).

Read [workflow](references/workflow.md) before implementing or diagnosing the game.

Capture the playable iframe and its actual request graph, not the portal page.
Prefer an author's downloadable web build or source repository when it matches the
live game. Capture the required files and use the bundle packer when needed.
The accepted result must work with all external network blocked.

Prefer source-level state, stable game events, saves, and DOM data. Keep the public
adapter small and separate from minified vendor code. Do not patch a large bundle
or build a pixel recognizer merely because it is quicker for one title; label any
unavoidable visual field as `visual_estimate`.

Keep captures, request logs, screenshots, source maps, and repair notes in the
Builder cache. The final folder contains only runtime files and no online fallback.
