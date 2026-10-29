# Workflow

## Discover narrowly

Start with the repository tools rather than ad-hoc browser injection:

```bash
python -m tools.unity_webgl discover games/benchmark/<game_id> --fingerprint
python -m tools.unity_webgl prepare games/benchmark/<game_id>
python -m tools.unity_webgl analyze games/benchmark/<game_id>
```

`prepare` remains useful when the optional static analyzer does not support the
metadata version. Keep all generated data under the project Unity cache selected
by the tool.

### Running `analyze` in a fresh environment

`analyze` needs two things, and only one of them ships with the repository.

**Il2CppDumper is vendored** at `tools/unity_webgl/vendor/il2cppdumper/`, so a
wiped run cache does not take symbol analysis with it. A copy staged under
`<cache>/backends/il2cppdumper/` still takes precedence if one exists; `--dumper`
overrides both.

**The .NET runtime is not vendored.** This bundled DLL targets .NET 6.0; use a
6.0.x runtime, not merely the latest LTS or an SDK. An existing runtime on PATH,
`GAMEENV_DOTNET`, or `~/.local/share/game2player/runtimes/dotnet/dotnet` is supported.
An explicit `--dotnet` path and the existing `<cache>/backends/dotnet/dotnet` lookup
remain available. Run `python -m tools.engine_inspect doctor` to check runtime versions.
See the root [installation instructions](../../../../README.md).

### Reading serialized resources

UnityPy is complementary to Il2CppDumper and does not require .NET:

```bash
python -m tools.unity_webgl assets list /path/to/game.data --type TextAsset
python -m tools.unity_webgl assets list /path/to/game.data --name level
python -m tools.unity_webgl assets show /path/to/game.data --path-id 123 --file sharedassets0.assets
python -m tools.unity_webgl assets show /path/to/game.data --path-id 123 --output <run-cache>/level.json
```

Use the file name/path ID actually returned by `list`. `--output` exports original
TextAsset bytes. `show` otherwise reads the object's serialized data; missing
MonoBehaviour type trees may prevent decoding. These are authored resource values,
not a live memory snapshot. Use the IL2CPP bridge for changing runtime state.

Review the existing profile population before inventing a new pattern:

```bash
python -m tools.unity_webgl audit-profiles
```

The audit identifies constant-only profiles, absolute roots, method-enabled
profiles, and the address strategies already in use; it does not validate gameplay
semantics.

Search the symbol database with narrow semantic hypotheses:

```bash
python -m tools.unity_webgl query <symbols.sqlite3> GameManager --kind type
python -m tools.unity_webgl show-type <symbols.sqlite3> GameManager
```

Do not open an entire generated dump or WASM text when a type/method query will do.
Generic methods may share an address: never join `dump_methods` and `wasm_methods`
by address alone. Treat only `alias_matched=true` as strong ABI evidence.

## Select state in evidence order

1. Direct TypeInfo/static fields or guarded object fields.
2. Side-effect-free getters with sound method/signature evidence.
3. Unity save/VFS data when it changes promptly enough for the task.
4. A thin adapter that derives semantic fields from the above.
5. Small visual estimates only when explicitly accepted as estimates.

Choose fields after understanding the game. Typical useful signals are current
level/wave/room, score, objective counters, player health/resources, position,
goal position, and terminal outcome. Do not expose raw address collections as a
public state contract.

The bridge supports TypeInfo/static reads, pointer chains, type guards, primitive
and IL2CPP string reads, safe getters, VFS, `SendMessage`, terminal rules, and
controlled reset actions. Validate a profile before runtime use:

```bash
python -m tools.unity_webgl validate-profile catalog/unity/<game_id>.yaml
```

No prebuilt profiles or game adapters are included. Derive the profile from the
captured build and validate its fields against controlled gameplay events.

## Runtime discovery fallback

When static analysis cannot locate a stable singleton, use the shared bridge rather
than copying raw `Module.wasmMemory` scans into another adapter:

```js
const classes = bridge.findUnityClasses("GameManager", {
  start: 1 << 20,
  limit: 8,
});
const candidates = bridge.findUnityInstances(classes[0].address, {
  start: 1 << 20,
  limit: 32,
  where: [{ offset: 24, type: "i32", equals: 1 }],
});
```

`describeUnityClass` returns the class name, self pointer, and static-fields pointer;
`isUnityInstance` rechecks an address and optional field guards. Always bound result
counts and scan large heaps in explicit windows; one call scans at most 32 MiB by
default. Treat zero or multiple candidates as unresolved unless an independently
understood field distinguishes the intended object. Class metadata near the class
pointer is excluded by default. Recheck after two resets and a scene transition;
freed objects can remain in WASM memory.

Some Unity versions keep a native cached pointer at object offset 8. When a game has
independent evidence for its native-pointer region, `cachedPointerMin` and
`cachedPointerMax` can reject metadata and stale candidates. Do not guess these
bounds merely to force a unique result.

These helpers are discovery/adapter primitives, not proof that an address is stable.
Prefer turning a validated candidate into a static-field chain or safe getter. If a
thin adapter must retain candidate addresses, revalidate them and rescan on scene
changes. Never promote an absolute heap object address into the profile merely
because it survived one reload.

## Reset and tasks

Prefer a native level/mode selector, a supported `SendMessage`, or a known save
snapshot. A query parameter may select a task variant, but the adapter must verify
that the requested variant actually became active. Avoid pointer writes unless the
shared bridge explicitly supports and guards the operation.

### The symbol database also answers "what replaces this click"

`analyze` builds `symbols.sqlite3` for any build that lacks one, and its
`dump_methods` table is not only for locating fields. When a task start needs menu
navigation, look up the method the button calls and call that instead:

```bash
python -m tools.unity_webgl analyze games/benchmark/<game_id>     # if no symbols.sqlite3 yet
python -m tools.unity_webgl show-type <symbols.sqlite3> <MenuClass>
```

The failure this replaces: an adapter that pages a menu with fixed-coordinate clicks,
waits a fixed `sleep` for a scene with no ready condition, and intermittently starts
the wrong variant. The menu's class usually has a public method for each button --
next, previous, confirm -- and often a static setter for a mode or difficulty; a
static method needs no instance at all. Calling these has no coordinates, no
scene-timing guess, and fails loudly -- either the method resolves or it does not --
instead of silently starting the wrong run.

Reach them through the bridge the page already has:

```js
window.__unityBridge.call(tableIndex, args)   // WASM table entry, args are numbers
window.__unityBridge.sendMessage(...)          // when the game exposes a receiver
```

`runtime.allow_method_calls` in `catalog/unity/<game_id>.yaml` does **not** gate
this. That flag governs whether the declarative *profile* may resolve a field
through a WASM call; `call` and `sendMessage` are primitives for the game's own
hand-written adapter, which runs in the page and could reach `runtime.table`
directly regardless. Keep mutation in the Builder-owned adapter, not the read-only
policy probe.

Two cautions when picking a table index: generic methods can share an address, so
never join `dump_methods` and `wasm_methods` by address alone, and treat only
`alias_matched=true` as strong evidence. Verify the call did what you expected by
reading the resulting state back, exactly as you would verify a query parameter.

## Evaluate

With external network blocked, verify title/start, input, and a real play event.
For every exposed field, capture before/after evidence tied to that event. Exercise
reset twice and check that stale pointers do not survive scene reloads. For each
task, use the Builder's common task-contract rules; Unity-specific evidence may
come from a direct named engine event even when normal play has not reached the
terminal outcome. Use one browser at a time and terminate it after each experiment.

## `Singleton<T>` keeps its instance in the base class

A class declared `Foo : Singleton<Foo>` stores the `static T instance` on the
*generic base*, not on `Foo`. Reading `Foo`'s own static area returns whatever
happens to sit there -- typically values in the tens of millions, whose shape is a
pointer rather than a field-offset mistake. Walk to the parent first:

```js
var PARENT = 44, STATIC_FIELDS = 92;          // 32-bit IL2CPP
function singletonFromParent(klass) {
  var parent = readPtr(klass + PARENT);
  var statics = parent ? readPtr(parent + STATIC_FIELDS) : 0;
  return statics ? readPtr(statics) : 0;      // static T instance is first
}
```

This also explains why sibling singletons behave differently: whether a class has
its own TypeInfo slot is unrelated to where its instance lives, so one
`Singleton<T>` subclass can be reachable by name while another is not.

## Confirm a candidate with a gameplay invariant

Class identity is not enough -- a scan returns stale and half-constructed objects
that pass a name check. Validate against a value the game constrains, then cache.
Use a field whose meaning and bounds are established from this game's source,
such as a current phase, bounded resource counter, or valid collection length.
Check that it changes after the corresponding gameplay event. The check costs one
read and is what separates a live instance from the other candidates a scan returns.
