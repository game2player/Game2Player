# Workflow

## Start from the profile

Read `catalog/unity/<game_id>.yaml` first. It gives you, already validated against
this build:

- `class_static_fields_offset` for the captured build;
- every `type_info` address Builder resolved, with the field path it reads;
- any `expected_type_info` guards it applies.

Reuse those addresses verbatim. They are build-specific, so never carry one from
another game, and re-check them if the packaged build changes.

## Bridge surface

`window.__unityBridge` exposes: `read(type, address)`, `readIl2CppString`,
`describeUnityClass`, `findUnityClasses`, `findUnityInstances`, `isUnityInstance`,
`resolveAddress`, `readVirtualFile`, `listVirtualFiles`, `tableFunction`, `call`,
`sendMessage`, `captureUnityInstance`, `status`, `getState`, `configure`.

`read` accepts `u8, i8, u16, i16, u32, i32, f32, f64`, plus `ptr` (an alias for
`u32`), `bool`, and `string` (which routes to `readIl2CppString`). Wrap every read
in try/catch: the bridge throws on an unmapped address, and one throw inside
`getState` costs you the whole sample and shows up as `states.policy_coverage`
below 1.0.

Only the readers belong in a policy probe. `sendMessage`, `call`, and `configure`
mutate the game or the trusted API.

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
Check that it changes after the corresponding gameplay event.

## Walking to data

The following illustrates a common 32-bit IL2CPP layout. Confirm the offsets
against the captured build rather than copying them as a game-independent contract:

```js
var STATIC_FIELDS = 92;                       // from the profile
function staticFields(typeInfo) {
  var klass = readPtr(typeInfo);              // TypeInfo -> class
  return klass ? readPtr(klass + STATIC_FIELDS) : 0;
}
// List<T>:  items ptr at +8, count i32 at +12, element i at items + 16 + i*4
// T[]:      length i32 at +12, element i at array + 16 + i*4
// string:   length i32 at +8, UTF-16 chars at +12
```

Bound every count before you loop on it. Clamp list and array sizes
to an appropriate maximum so a stale or misread pointer cannot
turn into a scan of the heap inside a synchronous `getState`.

Resolve a class name with `describeUnityClass(readPtr(addr))` to confirm you are
pointed at the object you think you are. Mapping real class names back to game
concepts is more reliable than trusting an enum index alone.

When the profile does not name the type you need, use `findUnityClasses` for the
class and `findUnityInstances` with an explicit field guard for the object, both
bounded, exactly as Builder's `unity-webgl` workflow describes. Treat
zero or multiple candidates as unresolved, and re-check after a reset and a scene
change -- freed objects survive in WASM memory.

## Know when the memory path has failed

If you cannot reach live instances, the probe will quietly report defaults and the
policy will look like it has state when it does not. Check explicitly, in the first
EVALUATE, that the fields that drive decisions actually change during play. A probe
whose board layout reports the same counts on every frame of every episode,
regardless of what is on screen, is reading defaults; that constant is the tell.

At that point the honest options are to spend the remaining budget on instance
discovery, or to accept that the game is being played from screen coordinates and
apply `policy-canvas-ui` -- not to keep adding TypeInfo constants.

## Pacing and startup

Unity games here are usually click-driven, so pair this with
`policy-canvas-ui` for the interaction side, and expect a
`step_interval_s` in the 0.2-0.3 range.

The bridge installs `window.gameAPI` when it is configured. If the run aborts with
`No actionable state ... after 60 seconds` and leaves no episode files, that is a
shared start defect for Builder, not something the probe can repair.
