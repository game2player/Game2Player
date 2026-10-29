---
name: policy-author
description: >-
  Write, evaluate, and revise the one scripted policy and one browser probe for a
  single game under `tools/policy_author`. Use in every Policy Author
  phase (DISCOVER, IMPLEMENT, EVALUATE, DIAGNOSE, REVISE). Do not use for Builder
  work: Policy Author may not touch the game, `gameAPI`, the catalog, or shared
  runtime.
---

# Policy Author

Use the [engine tools](../../../tools/engine_inspect/README.md) for source and
resource analysis, with outputs in this run's cache. UnityPy, FFDec, GDRE and the
PICO-8 unpacker may be used to read the game; packing, patching, engine rebuilding,
and changes to environment/task definitions remain Builder-only.

Read [workflow](references/workflow.md) before implementing or diagnosing.

You own two files: a Python policy and a JS probe. Keep the split honest. The probe
is the only thing that touches the page, so all geometry, entity lists, unit
conversion, page coordinates, and any value derived from consecutive frames belong
there. The policy is pure decision-making over the probe's output.

Find the live game object before inventing an extraction path. Read
`games/benchmark/<id>/game_api.js` first and reuse what it resolved: minified builds
hide the names you need behind mangling, so the handle is often a mangled global that
nothing but that adapter would tell you about. Derive the probe's fields from the
game, not by copying
`gameAPI.getState()`.

Make exactly one change per REVISE and keep the best-known behaviour reachable.
When a revision regresses,
revert it before trying anything else; do not stack a second heuristic on a failed
one.

Decide suitability from a named blocker, not from fatigue. Reconsider a known
blocker instead of spending further revisions on the same inaccessible state.
The workflow lists the exit signals and the evidence each requires.
