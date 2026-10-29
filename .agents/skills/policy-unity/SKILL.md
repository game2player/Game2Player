---
name: policy-unity
description: >-
  Build the policy probe for a confirmed Unity WebGL game by reading
  IL2CPP memory through the already-installed `window.__unityBridge` bridge and
  the verified type addresses in `catalog/unity/<game_id>.yaml`. Use alongside
  policy-author for Policy Author work only. Builder's own Unity skill is
  unity-webgl; do not use this one to construct an environment.
---

# Unity WebGL policy probes

Read [workflow](references/workflow.md) before implementing or diagnosing.

The bridge already exists. `tools/unity_webgl/bridge.js` installs
`window.__unityBridge` as a non-writable property before the game loads, and the
probe reads memory through it -- do not scan `Module.wasmMemory` yourself and never
modify the bridge.

Builder has already paid for the hard part. `catalog/unity/<game_id>.yaml` carries
`class_static_fields_offset` and the validated `type_info` addresses for that build.
Start from a profile address rather than hunting your own.

The failure mode to avoid: knowing a TypeInfo but never reaching a live instance
through it. Type addresses added on top of the profile that never yield a live object
leave every derived field empty or constant on every frame, and the policy falls back
to fixed screen coordinates -- many revisions for one task. A type address you
cannot walk to an instance is not progress; say so and reconsider suitability.

The probe is read-only. `read`, `describeUnityClass`, `findUnityClasses`,
`findUnityInstances`, `isUnityInstance`, and the VFS readers are legitimate.
`sendMessage`, `call`, and `configure` change the game and are out of bounds.
`bridge.getState()` returns trusted state; do not launder it into the probe.
