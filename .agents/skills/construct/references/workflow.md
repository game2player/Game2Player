# Construct workflow

## Reaching the runtime

`window.cr_getC2Runtime()` returns the live runtime object; call it rather than
reading any global the minifier may have renamed. Construct 3 exports use
`c3runtime.js` and a different accessor, so confirm which you have before copying
field names.

```js
const runtime = typeof window.cr_getC2Runtime === "function"
  ? window.cr_getC2Runtime()
  : null;
```

## What the runtime holds

| what you want | where |
| --- | --- |
| current layout | `runtime.running_layout` (fall back to `runtime.layout`) |
| game time | `runtime.kahanTime.sum`, in seconds -- multiply for `gameTimeMs` |
| paused | `runtime.isSuspended === true`, or `runtime.timescale === 0` |
| object types | `runtime.types`, keyed by the type name from the editor |
| live entities | `runtime.types[name].instances`, an array |

Instance positions are `.x` / `.y` in layout coordinates, and instance variables
live on the instance itself under the names given in the editor. Enumerating
`runtime.types` once and logging the key list is the fastest way to learn a build's
vocabulary; the names survive minification because Construct stores them as data.

## Notes

- `kahanTime.sum` is a compensated running sum of frame deltas, so it is the game's
  own clock and pauses when the game does. Prefer it to `performance.now()` for
  `gameTimeMs`.
- A layout change is the natural level/room boundary. `running_layout.name` is a
  stable progress signal for level-shaped tasks.
- Construct games routinely persist to `localStorage`. Clear the game's own keys on
  reset if the task must start clean, and remember that the harness's
  `__resetRandomSeed()` only reseeds `Math.random`, not any generator Construct
  serialized into a save.
