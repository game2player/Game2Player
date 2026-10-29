---
name: godot
description: >-
  Instrument a Godot 3/4 HTML5 export as a game environment, including PCK
  recovery and a JavaScriptBridge state hook. Use only after Builder has identified
  Godot; do not use for native Godot builds, which cannot be packaged here.
---

# Godot Builder

GDRETools is bundled. Use `python -m tools.godot
--recover=game.pck --output=<run-cache>/recovered` for GDScript and scene recovery,
or `--list-files=game.pck` to inspect its contents. Arguments follow upstream GDRE;
the entry point runs headless. See the [tool index](../../../tools/engine_inspect/README.md).

Identify the engine from a `GODOT_CONFIG` object in `index.html` plus an
`index.js` / `index.wasm` / `index.pck` trio. Confirm the encoded engine version from the PCK or matching export metadata
before choosing tooling; `GODOT_CONFIG` alone need not name a version. Threaded
web exports need cross-origin isolation headers; do not infer the requirement
solely from the major version.

**A native export is not buildable here.** If the only downloads are Windows/macOS/
Linux binaries, return `unsuitable` rather than attempting to convert one; recovering
a project from a desktop PCK and re-exporting it to web is a separate undertaking,
not a build phase.

GDScript runs inside the WebAssembly engine, while scripts and scene resources
are stored in the PCK; there is no ordinary page JavaScript object graph to read. The route that works is to add a small hook to the game's own source
and rebuild the PCK. Read [workflow](references/workflow.md) before starting -- the
whole approach hinges on getting the order right.
