---
name: policy-pico8
description: >-
  Build the policy probe and input timing for a browser-exported PICO-8 cartridge:
  what is readable from GPIO and base RAM in the Emscripten heap, what is not, and
  the suitability call. Use alongside policy-author for Policy Author
  work. Builder's cartridge-packaging skill is pico8; this one does not
  build environments.
---

# PICO-8 policy probes

Read [workflow](references/workflow.md) before implementing or diagnosing.

The cartridge's Lua object tables are not exposed as directly addressable
JavaScript objects. Base RAM contains map tiles, sprite flags, draw state including
the camera, the bytes the cart pokes into GPIO, and the screen. Entity positions
generally are not. Decide in DISCOVER whether your policy needs a live entity
position. If it does, do not start implementing against pixels: return
`needs_environment` with `request_kind` observation and name every position and
value the policy needs. Builder can patch the cartridge to poke them into GPIO;
resume against `window.__policyObservation` once the handoff arrives.

Find base RAM with a multi-byte signature and an independent cross-check: match
several bytes the cartridge is known to hold, then confirm the candidate against a
second, independent value. A single-byte landmark is not a signature -- and a byte
taken from the host page rather than the cartridge (a `pico8_gpio[...]` assignment in
`index.html`, say) will never match the heap at all. When the scan misses, re-scanning
128 MB inside every `getState` drops sampling to a fraction of the configured rate.

Do not reach for canvas pixels as a substitute. Object identities read from pixels
are wrong often enough that tightening the classifier destroys the only working move
while loosening it makes the policy act on the wrong object.

PICO-8 polls input once per frame, which changes what actions work. See the
workflow for click width and jump-key edges.
