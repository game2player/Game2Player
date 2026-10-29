---
name: unity-webgl
description: Build and validate one confirmed Unity WebGL game as a self-contained game environment using the repository's IL2CPP discovery, bridge, profile, state, reset, and task tooling. Use only after Builder has identified Unity WebGL; do not use for native Unity players or generic HTML5 games.
---

# Unity WebGL Builder

Use the existing shared base under `tools/unity_webgl`; do not replace or extend it
from a per-game build. Choose the analyzer by the question: Il2CppDumper via
`analyze/query/show-type` for types, fields and methods; UnityPy via `assets list/show`
for serialized objects, level configuration and TextAssets. Resource initial values
are not live game state. Read [workflow](references/workflow.md) before implementing
or diagnosing the game.

Keep per-game work thin:

- packaged runtime in `games/benchmark/<game_id>/`;
- reviewed bridge profile in `catalog/unity/<game_id>.yaml`;
- a small `game_api.js` adapter only when the shared profile cannot express menu
  startup, reset, or derived public fields;
- game/task catalog YAML in their normal locations.

Prefer actual IL2CPP state through the bridge. Treat save/VFS data as storage and
frame-buffer classification as `visual_estimate`, never as internal state. A large
pixel recognizer is evidence that the engine path was not solved, not a reusable
Unity solution.

A constant-only profile proves bridge injection, not gameplay instrumentation.
Before adding more per-game memory enumeration, use the bridge's bounded class and
instance discovery helpers described in the workflow. Their results are candidates:
they still need stable type/field evidence and reset validation.

Never copy Unity analysis caches, decompressed builds, dumps, symbol databases,
downloaded runtimes, or screenshots into the game directory. Never modify the
shared bridge to rescue one title. Keep reusable findings in the Builder's bounded
working notes for separate review.
