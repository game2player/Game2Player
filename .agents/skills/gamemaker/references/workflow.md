# GameMaker workflow

## The three handles

A GameMaker HTML5 export keeps its object system behind short minified globals.
Locate these roles in the captured build; the names below are illustrative:

| handle | shape | use |
| --- | --- | --- |
| `instanceAccessor(objectIndex)` | function returning an instance array | enumerate live instances of one object type |
| `roomAccessor()` | function returning an int | current room index |
| `globalNameMap` | object mapping source names to mangled global-variable keys | resolve a GML global by its authored name |

**These exact names are not portable.** They are what one minifier run produced;
another export of the same engine version can use different letters.

## Finding this build's handles

Work from the shape, not the name. Locate the instance accessor in the source
with narrow `rg` queries on the JavaScript, then confirm its known signature
and result. Do not invoke arbitrary `window` functions to test their shape: some
change rooms, reset state, or have other side effects. The expected accessor takes
an object index and returns instances carrying `x`, `y` and an `id`-like field. The room accessor is usually a method on a small object that also
holds room-name data. The name map's signature is an object whose values are strings that
all start with the same prefix (commonly `gml`).

Once found, record the mapping in `working-notes.md`; it is the single most
expensive thing to rediscover in this engine.

## Globals

GML globals land on one container object under mangled keys. Resolve them through
the name map. If the captured source shows a plain GML prefix as well, check that
form when the name map has no entry:

```js
const key = (globalNameMap && globalNameMap[name]) || name;
if (Object.prototype.hasOwnProperty.call(globalObj, key)) return globalObj[key];
const fallback = "gml" + name;          // some builds skip the map entirely
if (Object.prototype.hasOwnProperty.call(globalObj, fallback)) return globalObj[fallback];
```

## Notes

- Room index is the natural level signal. Map it to a human level number through the
  room *name*, not the raw index: GameMaker projects interleave menu, transition and
  cutscene rooms with playable ones, so indices are not contiguous levels.
- Instance positions are `x` / `y` in room coordinates; instance variables sit
  directly on the instance under their authored names.
- These exports commonly persist progress in `localStorage`. Decide explicitly
  whether a task starts from a clean slate and clear it in `reset()` if so.
