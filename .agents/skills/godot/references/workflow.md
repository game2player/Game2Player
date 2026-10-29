# Godot workflow

## The route

1. **Recover the scripts.** `python -m tools.godot --recover=index.pck
   --output=<run-cache>/recovered` uses bundled GDRE to recover GDScript and scene
   metadata. For one script, first list the PCK and then recover its stored path:
   `--recover=index.pck --include=res://scenes/main.gdc --output=<run-cache>/recovered`.
   Match `.gdc` if that is what the PCK contains, not the recovered `.gd` name.
   Do not combine `--scripts-only` with `--include`; use the include filter alone.
   The entry point resolves local paths before GDRE changes its working directory;
   `res://` include patterns and patch destinations are left unchanged.
   Work on a copy under the run cache; never patch the captured PCK in place.
2. **Write the hook in GDScript**, in the game's own singleton or main scene, next
   to the state it reports. Publish through `JavaScriptBridge` (Godot 4; it is
   `JavaScript` in Godot 3) so the page can read a plain JS object.
3. **Recompile to bytecode for the exact engine version** the export was built with
   -- read the full version from the PCK/export metadata, not just Godot 3 or 4.
   `--list-bytecode-versions` shows GDRE's supported compilers. For a 4.5.0 export,
   use `--compile=<run-cache>/main.gd --bytecode=4.5.0 --output=<run-cache>/compiled`,
   then `--pck-patch=index.pck --patch-file=<run-cache>/compiled/main.gdc=res://scenes/main.gdc
   --output=<run-cache>/patched.pck`. If that version is not supported by GDRE, use
   the matching Godot compiler in the run cache instead; do not rebuild the original
   web runtime with a different engine version. Patch only the changed resources.
4. **Index the adapter before the engine boots.** `game_api.js` must be loaded by
   `index.html` ahead of `index.js`, so the bridge target exists when the first hook
   call fires.
5. **Reset by reloading the same URL** with a fresh-start query the hook honours.
   Godot's own `user://` saves survive a reload, so the hook has to clear them or the
   second episode starts mid-run.

## Publishing state

Keep the hook a projection of values the game already computes; do not recompute
game logic in it. Expose native completion flags, the current level/menu phase,
and the player's spawn state. Latch completion before the game's own save-and-reset
if that reset would erase the transition the task verifies.

Latch terminal events at the source. A Godot game that returns to its title screen
on completion destroys the state you needed to observe; set the flag in the same
frame the event happens, not by polling afterwards.

## Cross-origin isolation

Godot web exports built with thread support need `SharedArrayBuffer`, which needs
`Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy:
require-corp`. `GODOT_CONFIG.ensureCrossOriginIsolationHeaders` makes the export
register a service worker to add them, which is fragile under capture -- prefer
serving them directly through `_headers.json`. If `Engine.getMissingFeatures()`
reports missing threads, this is why.

Also map the compound extensions the export ships (`.pck`, `.wasm`, and any `.gz`
variants) in `_headers.json`, or the loader fails before the first frame.

## Using decompiled data honestly

Scene metadata and authored debug waypoints recovered from the PCK are legitimate
for *bounding* a check -- deciding where a goal is, so a bounded trigger can
be aimed at it. They are not a substitute for the event: do not teleport the player
into a goal and record the resulting transition as normal play. The main Builder
prompt's rule applies unchanged -- a debug trigger proves the instrumentation, never
the task.

Do not attempt to play through the level. Use bounded checks to verify the state
hook and controls; route-finding and solving belong to the player and Policy Author.
