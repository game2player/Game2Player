---
name: pico8
description: >-
  Package, instrument, and validate one browser-exported PICO-8 cartridge as a
  self-contained game environment using the player lifecycle and GPIO state
  channel. Use only after confirming the PICO-8 HTML export or runtime; do not use
  for lookalike pixel-art games or other fantasy consoles.
---

# PICO-8 Builder

The shared cartridge tool is public: `python -m tools.pico8 --help`. Use
`python -m tools.engine_inspect inspect games/benchmark/<game_id>` to locate player
scripts, including ZIP members; see the [tool index](../../../tools/engine_inspect/README.md).

Read [workflow](references/workflow.md) before implementing or diagnosing the
cartridge.

Keep the browser player and cartridge local. Prefer an official HTML export or a
source release that matches the playable build. Treat `pico8_gpio` as transport,
not automatic game state: a pin is meaningful only when cartridge code writes it
and a controlled game event proves its semantics.

Use a thin per-game adapter for pin mapping and state normalization. Do not copy
the repeated player lifecycle into every metric: startup, running/frame detection,
pause detection, and reload reset should remain separate from game-specific GPIO
fields. Never replace an unknown pin with elapsed wall time or a guessed visual
counter.

Change cart code only through the shared `python -m tools.pico8` (unpack to .p8,
edit, pack), never by hand-editing `_cartdat` or with a one-off script. It re-encodes
only the carts that changed and refuses an edit that pushes a cart over the token or
ROM limit, or that removes the definition of a name the code still reads, which
loads fine and crashes at the first action that reaches the use. Out of tokens, lift
the player's token limit (`lift-token-limit`); never delete game code to make room.

Keep extracted cart source, patch notes, captures, and screenshots in the Builder
cache. The final game folder contains only the playable export and its small runtime
adapter.
