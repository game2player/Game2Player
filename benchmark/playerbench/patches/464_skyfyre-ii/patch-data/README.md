# Inspected ActionScript bridge recipe

The patch applies only to root SWF DoABC tag 363 (zero based), after zlib
uncompression. It copies 461,976 bytes from the downloaded original tag and
inserts 56,612 bytes of compiled bridge code, constant-pool entries, and
compiler relocation/metadata edits. The longest literal run is 2,414 bytes.
All other 368 tags, including every original asset, are copied unchanged.
This is not a diff against a compressed SWF or a modified game overlay.

The result is byte-identical to the existing PlayerBench reference when
uncompressed (SHA-256
8138681d1ce13fb8c1e49e0aabe1a2af0f0cd6444d1c426a544a6375b006ac2d).
The downloaded compressed original, original ABC tag, reconstructed ABC tag,
and final uncompressed movie are checked by SHA-256 during installation.
Both original ActionScript strings of at least 128 bytes are copied entirely
from the download; no asset payload is inserted.

`bridge-additions.as` is a review extract of only the authored `sky*` methods
from JPEXS FFDec 26.3.0 export of the inspected reference. It is not a standalone
game source file or needed for installation. The binary recipe also retains
the existing adapter's fields and call sites:

- Preloader: permit the local loopback origin through the original sitelock.
- StateModule: fresh native character creation, selected stage, native state,
  transition-aware visible UI geometry/text, and ExternalInterface callbacks.
- PlayObjectsMediator: native combat state/visible geometry; attempt, spawn,
  and actual native victory events; initial cursor at (120, 240).
- Enemy: actual HP loss event, preserving the original game's double damage
  subtraction in Actor.applyHit and Enemy.applyHit.
- PlayMediator: the reference's keyboard focus restoration after native Continue.
- PauseView / TransitionView: read native panel/transition readiness.
- PlayObject / Hero: visible rendered/collision geometry and visible indicators.
- ComplexButton, MapButton, ItemButton, ItemEquipView, ItemShopMediator,
  SoundView: read visible menu state and sound controls.

The range recipe includes compiler relocations in those classes. No checkpoint
or debug callbacks are included. No game/task definitions, damage constants,
enemy waves, scoring targets, or win conditions are changed. This faithfully
packages the existing adapter rather than recompiling or repairing native code.
The patch uses Python standard-library SWF framing and zlib only; no engine or
ActionScript interpreter is implemented. JPEXS is an audit tool, not an install
dependency. Ruffle is obtained separately from its official pinned release.
