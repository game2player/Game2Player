# Workflow

## Coordinates belong in the probe

Emit page coordinates from the probe, never from the policy. The pattern:

```js
function designToPage(canvas, dx, dy) {
  return {
    x: canvas.left + (dx * canvas.width) / DESIGN_W,
    y: canvas.top + (dy * canvas.height) / DESIGN_H,
    canvas_x: dx,
    canvas_y: dy
  };
}
```

`canvas` comes from `getBoundingClientRect()` on `#unity-canvas` or the first
`canvas` element. Return both the page pair and the canvas pair on every target, so
a DIAGNOSE can compare what was clicked against a frame without redoing the maths.

Prefer a live object over `DESIGN_W`-space constants for `dx, dy`. When the engine
exposes the widget, ask it -- a button's world position, or an entity's world
coordinates converted through the camera. When it does not, and the run can read
top-level game data but never resolves the per-widget instances, the probe's layout
reports placeholder types and its slot lists stop reflecting the board: you are
guessing, and the cost is a full DIAGNOSE-REVISE-EVALUATE cycle per guess. Say so in
DISCOVER rather than after several revisions.

## Verify a hit target once, cheaply

Hunting one button by guessing coordinates costs a revision per guess, and hundreds
of clicks can land on empty board next to the widget without anything erroring. Every
such guess is settled by looking at a recorded frame. Look at the frame first.

## Choose the right action kind

`click` is a same-tick press and release. Games that poll input once per frame can
miss it entirely -- a run can produce hundreds of instantaneous clicks that do
nothing while its slower drags visibly move things. Switching the confirm action to
`click_hold` with `duration: 0.2` is the fix. If drags register and clicks do not,
the problem is the pulse width, not the position.

For `drag`, grab only the element the game lets you grab. Grabbing a card buried
under others makes the engine move the whole pile and reject the move, and the board
freezes for as long as the policy keeps trying. Restrict the source to the exposed
head of a stack.

Check the catalog's control notes for the confirm/advance key rather than guessing.
A key that is a no-op in one build submits the turn in another, and pressing the
wrong one does not error -- it just never advances.

## Detect accepted-but-inert actions

An action the engine accepts but that does not advance the game is the hardest case
to see, because nothing errors. Watch a trusted counter, not the screen. A move into
an occupied cell can be legal as an animation and still never end the turn, leaving
the turn counter frozen for the rest of the episode; a fallback branch whose real
match has already failed will repeat one target coordinate indefinitely.

Guard against both in the policy: refuse to repeat the same target when the trusted
progress field has not moved since the last attempt, and when the current hand or
board cannot satisfy the intended move, take the different action (end the turn,
draw, skip) instead of falling through to the nearest thing that is clickable.

## Pace

Give the UI time. `step_interval_s` in this class of game belongs around 0.2-0.5.
A short interval mostly produces repeat clicks during an animation the probe cannot
see through.

Track your own placement count against a trusted counter, not against your own
intent. Per-turn state cleared only in `reset()` is stale from the second turn on,
and the policy ends up repeating the same end-of-turn click forever. Zero it when
the trusted turn counter rises.
