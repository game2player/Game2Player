# ActionScript bridge patch

`abc-ranges.json` describes copy/insert operations against the two decompressed
DoABC payloads at root tag indices 383 and 3648 (zero based). `abc-insert.bin`
contains 94,277 bytes of compiled bridge instructions, new constants, and
compiler relocation/metadata edits. It is not a SWF or a compressed-movie diff.
1,812,904 bytes of the resulting ABC payloads are taken from the user's original
download. All other 3,650 tags are copied unchanged.

The patched result reproduces the existing modified reference after SWF
uncompression. Original media/asset tags never enter this package. The 65
ActionScript string constants of 128 bytes or more were also checked: every
byte is reused from the download, with no literal replacements. The longest
literal run is 7,582 bytes, in compiled ActionScript rather than an asset tag.

`bridge-additions.as` is a readable review extract of only the authored gw*/po*
helper methods from the inspected output, exported using JPEXS FFDec 26.3.0.
It omits the native game classes and their existing methods. It is not needed
at installation. The binary recipe also includes the narrow call sites and
fields for those helpers:

- Main: native mission setup, read-only state/event bridge and visible-object
  observations, initial native briefing/tutorial dismissal, frame clock.
- Mc2_Actor: observe actual post-armor damage and ordinary enemy deaths.
- MC2_Game: publish the existing native Victory event.
- BriefingScreen / TutorialScreen: expose initial native phrase advance/Skip.
- TeaGamesPreloader: defer its native load callback until bytes finish and the
  next frame; bypass the network-dependent advertising preloader.
- FactoryObject: observation identity across native pool reuse.
- AmmoManager: observe native contact geometry.

No debug/factory/goal-changing test callbacks are present. The existing visible
observation bridge is separate from the task evaluator. Scoring, controls,
mission defaults and lifecycle in overlay/game_api.js remain byte-identical to
the reference. These ABC edits were originally compiled with JPEXS FFDec;
installing the checked range recipe requires only Python's standard library.
