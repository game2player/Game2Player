# Defold workflow

## What is reachable

Live Lua tables are not normally exposed as page JavaScript objects. This does
not make the script assets inaccessible: `.arci`/`.arcd`/`.dmanifest` archives
contain compiled resources, including Lua source or bytecode depending on the build.

For static recovery, [arcdEx](https://github.com/jht3QAQ/arcdEx) can extract supported
archive versions; it is a candidate, not an installed or universally compatible
backend. HTML5 uses the Lua 5.1 family, not LuaJIT. After extracting the script's Lua
payload, standard bytecode may be readable with [unluac](https://sourceforge.net/projects/unluac/)
when its format/debug-information requirements are met. This does not automatically
provide live state or a valid repacking route.

Without a source hook, two persisted-state surfaces are practical:

1. **The Emscripten virtual filesystem**, where Defold's `sys.save` writes.
2. **`localStorage`**, which the IDBFS/`localStorage` backend mirrors saves into on
   some builds.

## Reaching the filesystem

```js
const fs = (window.Module && window.Module.FS) || window.FS || null;
const root = (window.DMSYS && typeof window.DMSYS.GetUserPersistentDataRoot === "function")
  ? window.DMSYS.GetUserPersistentDataRoot()
  : "/data";
```

`DMSYS.GetUserPersistentDataRoot()` is Defold's own accessor and is the correct
source; `/data` is the default it almost always returns, and is a reasonable
fallback but not a substitute. From there, `fs.readdir(dir)` and `fs.stat(path)`
walk the tree -- guard the recursion with a depth limit, and detect directories with
`fs.isDir(mode)` where available, falling back to `(mode & 0x4000) === 0x4000`.

Defold save files are its own binary table format, not JSON. Read the bytes and
locate the fields you need by their surrounding literals, or -- usually better --
find where the game writes the value and instrument at the source of the write.

## Consequences for task design

- Progress that is only written on save is not continuous. If the game saves once
  per level, your progress signal has the granularity of a level, and the honest
  `progress_signal.milestones` are the level boundaries, not a made-up curve.
- Saves persist across reload, so a reload-based reset must clear them explicitly
  or every task after the first starts mid-run. Clear both the filesystem path and
  any mirrored `localStorage` key.
- Prefer a value the game already persists over one you have to infer. A counter the
  game saves is a real game-owned measure; a pixel estimate is not, and the
  provenance taxonomy in the main Builder prompt ranks it accordingly.

## Notes

- `dmloader.js` fetches the archive in shards listed by `archive_files.json`.
  Capture all of them or the game stalls at its loading bar with no error.
- The module takes noticeable time to instantiate. Drive `is_actionable` from a real
  readiness signal in the game, not from a timer.
