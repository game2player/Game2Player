---
name: policy-canvas-ui
description: >-
  Drive a policy that plays through mouse clicks and drags on a canvas
  with no DOM hit targets: card games, board games, turn-based tactics, and
  build/management UIs. Use alongside policy-author whenever the policy
  must emit click / click_hold / drag at screen coordinates. Not needed for
  keyboard-only games.
---

# Canvas UI policies

Read [workflow](references/workflow.md) before implementing or diagnosing.

The action executor takes page coordinates, so the probe must resolve them: read
`canvas.getBoundingClientRect()` and return `page_x` / `page_y` on every clickable
thing.

Resolve each target from a live game object, not from a design-space constant. A
probe that reads the engine's own widget sprites and converts each object's world
position converges in about one revision. A probe that lays out fixed coordinates
because no UI object resolves spends most of its revisions changing which pixel to
click, and still loses.

A click at the wrong pixel and a click the game ignores look identical from the
outside: the trusted score does not move. Before spending an episode on a guessed
coordinate, confirm it against a captured frame. Once the policy is running, treat
a long run of identical actions with no trusted change as a signal to change the
*kind* of action, not to nudge the coordinate again: hundreds of clicks at one point
with the trusted score unmoved usually means the clicks are not being seen at all.
