---
name: builder
description: >-
  Cross-engine knowledge for turning one browser game into a game
  environment: the canonical state contract the runner actually enforces, the
  runtime hooks the launcher installs, and the traps that have cost real Builder
  runs. Use in every Builder phase alongside the engine-specific skill. Do not use
  for Policy Author work.
---

# Builder

The [engine tool index](../../../tools/engine_inspect/README.md) lists the bundled
analyzers and when to use them. Select the relevant engine tool instead of finding
or downloading another copy per game. `engine_inspect inspect` can list ZIP assets;
`doctor` checks local runtime requirements when needed.

Read [workflow](references/workflow.md) before implementing or diagnosing. It holds
the concrete contracts and the per-trap detail; this page is what to keep in mind
the whole time.

**Produce the state the runner checks, not a plausible-looking one.** Nine fields
are mandatory and the gate rejects the artifact without them: `schemaVersion`,
`gameId`, `timestampMs`, `gameTimeMs`, `status`, `is_actionable`, `terminal`,
`game_state`, `metrics`. `is_actionable` is the one that silently ruins runs --
collection waits on it for 60 seconds and then records no episode at all, so an
environment that never flips it produces nothing rather than producing something
bad.

**Prove a field means what you say by making it move.** A path that exists and
holds a number satisfies every automated check, even when the number counts
something else entirely. The defence is
not another assertion, it is causing the real event with a bounded debug trigger and
recording the before/after you saw.

**Enter a task from inside, not by clicking.** A start driven by screen
coordinates and a `sleep` is guessing at a machine you can address directly. Every
engine here exposes the thing the button does -- a query parameter it already
honours, a method in its own symbol table, a runtime handle `game_api.js` has
already resolved. Fixed clicks through a menu start the wrong variant often enough
to fail evaluation, while the game itself usually holds the function that button
calls. The workflow lists the direct entry
for each engine.

**You are not the player.** Confirm that controls reach the game and that state
responds, then stop. Route-finding, level-solving and frame
scrubbing belong to Policy Author, which has an iteration budget shaped for them.

**Some defects are not yours to find.** A progress counter that never latches, a
deadlock that appears only after many rounds, a high-water mark that breaks when the game
shrinks its own list -- these only appear during sustained play, and Policy Author
reports them back through `needs_environment`. Build so that such a report is cheap
to act on: instrumentation in one readable `game_api.js`, source patches minimal and
written down.

**Write down what you learned, in `working-notes.md`.** Findings left only in a
phase result are read once and never again. Record build-specific discoveries,
including capture restrictions and the native entry points used by the adapter.
