---
name: flash
description: Build and validate one confirmed Flash SWF as a self-contained game environment using the bundled Ruffle runtime, ActionScript/ExternalInterface inspection, save state, and task instrumentation. Use only for real SWF payloads, not HTML5 remakes or pages that merely embed missing Flash content.
---

# Flash Builder

FFDec is bundled: `python -m tools.flash -export script <run-cache>/scripts game.swf`
recovers ActionScript. Use `python -m tools.flash -help` for upstream operations.
It needs Java; `python -m tools.engine_inspect doctor` reports local availability.
See the [tool index](../../../tools/engine_inspect/README.md) for paths and ZIP inputs.
Recovered code helps locate state but does not itself expose live variables.

Read [workflow](references/workflow.md) before implementing or diagnosing the game.

The official Ruffle 0.4.1 self-hosted distribution is bundled separately from
FFDec. Install it into the generated game, using that game's actual directory:

```bash
python -m tools.flash ruffle-runtime games/benchmark/1_my-game/ruffle
```

This extracts the complete local distribution, including both upstream licenses;
it needs no download or existing game. The destination must not already exist.
See [Ruffle provenance](../../../tools/flash/vendor/RUFFLE.md) for the pinned
source and the workflow for the launcher pattern.

Package the actual SWF and this local `ruffle/` directory. A JavaScript wrapper
that calls an ExternalInterface method is not proof that the SWF implements it. Verify the symbol or ActionScript path and then prove
that values change during real play.

Prefer existing ActionScript state, ExternalInterface callbacks, and SharedObject
save data. Keep any JS adapter thin and label save-derived fields as `storage`.
Only patch bytecode when the change is narrow, reproducible, and checked against
real interaction; otherwise stop instead of accumulating one-off hacks.

Do not put decompiler output, analyzer tools, screenshots, reports, or captures
in the game folder. Optional decompilers belong outside the repository and their
absence is not permission to claim state without evidence.
