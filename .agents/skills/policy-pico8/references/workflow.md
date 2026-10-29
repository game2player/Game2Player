# Workflow

## Read cartridge source with the shared tool

Locate the player with `python -m tools.engine_inspect inspect
games/benchmark/<game_id>`, then `python -m tools.pico8 unpack
<player-or-bundle.zip:member> <run-cache>/carts`. Inspect the resulting `.p8` source
to identify pin writes, map conventions and RAM landmarks. This only writes the
cache: Policy Author must never `pack`, lift token limits, or modify the player.
Host-written GPIO pins are not proof that the cart uses those RAM bytes.

## Two state channels

`window.pico8_gpio` (with `window.gpio_data` as a fallback name) is what the
trusted API already reads. A pin means something only if the cartridge pokes it;
the host page writes some pins itself. Read pins for the same coarse facts the
trusted API uses -- level, counters, alive/dead -- and take everything else from
RAM.

Base RAM lives somewhere in the Emscripten heap (`Module.HEAPU8`). Locate it once,
cache the offset, and revalidate the cached offset on every call before using it:

```js
// SIGNATURE: [address, byte] pairs taken from this cart's own data, such as its sprite flags
function ramMatchesGpio(heap, base, pin, value) {
  return SIGNATURE.every(([address, byte]) => heap[base + address] === byte)
      && heap[base + 0x5f80 + pin] === value;   // a nonzero pin the cart itself writes
}
```

The shape of a working signature: several bytes the cartridge is known to have in
sprite-flag memory, plus one byte cross-checked against a value GPIO reports
independently. Scan the low 8 MB first and the rest only on a miss, and stop at
`heap.length - 0x6100` so a near-end match cannot read past the buffer.

Two things that do not work. A single-byte landmark is not a signature, and if the
byte was written by the host page rather than the cartridge it matches nothing at
all -- while the failed scan runs over 128 MB on every `getState` and cuts sampling
to a fraction of the configured rate. A landmark taken from values the cart pokes
every frame matches *copies* of that region tens of megabytes up, at a different
address on every frame, and decodes confident nonsense.

## What you can and cannot read

Reachable, and used successfully: map tile memory (with `fget`-style flag lookup
from sprite-flag memory to classify solid/ice/hazard tiles), the draw-state camera
(a signed 16-bit pair at `base + 0x5f28` and `base + 0x5f2a`), any byte the
cartridge pokes, and the screen at 0x6000.

Not reachable: the Lua tables holding the player and other objects. Synthesising the
player position from the camera with per-level constants is the usual fallback, and
it is also the usual reason a run never finishes: the estimate is off by enough that
the same obstacle defeats every revision of the movement logic. If the game needs
pixel-accurate entity positions, this is a
suitability problem, not a tuning problem, and it is visible before the first
revision.

## Input timing

The cartridge polls input once per frame, so the pulse width of an action matters
more than in a DOM game.

- An instantaneous `click` (press and release in the same tick) can be missed
  entirely, while slower drags on the same page visibly work. `click_hold` with
  `duration: 0.2` is the fix. If drags land and clicks do not, widen the pulse
  before moving it.
- Jump-style keys usually arm on the rising edge. A held key produces one jump and
  then nothing, and the character sits at the same tile for the rest of the episode
  because there is no new press. Pulse the key and keep a cooldown so it can rise
  again.
- The hold length is itself a control parameter, not a constant to pick once. Where
  holding the jump key near the apex slows the fall, a short pulse clears a wall but
  falls short of a gap, a long one clears the gap, and applying the long one
  globally re-breaks the wall. If you make it position-dependent, keep the value
  that already works for the earlier obstacle.

## Startup

If the packaged player cannot boot the cartridge, nothing above applies. The
run aborts before any episode is written -- typically a `PROGRAM TOO LARGE` banner
on screen with cartridge frames stuck at 0, GPIO empty, and trusted `is_actionable`
false for the whole 60 s readiness wait.
That is a shared start defect and belongs in a `needs_environment` request, not in
the probe.
