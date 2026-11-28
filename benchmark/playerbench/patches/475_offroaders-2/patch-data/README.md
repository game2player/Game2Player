These BSDIFF40 files apply only to decompressed DoABC payloads (SWF tag type 82),
never to the compressed movie or asset tags. They were generated with bsdiff4
1.2.6 after comparing a fresh Armor Games download with the modified reference.
The patcher implements standard BSDIFF40 decoding using Python's bz2 module;
bsdiff4 and FFDec are not installation dependencies.

| Tag | Patch bytes | Bytes derived from original | Fresh compiled bytes |
| --- | ---: | ---: | ---: |
| 70 | 6,001 | 234,132 | 2,771 |
| 3908 | 12,271 | 824,357 | 3,821 |

The original-derived stream contains mostly zero adjustments, with 5,436 and
14,468 nonzero bytes respectively. Those adjustments account for recompiled
constant-pool, namespace, method and bytecode operand references. Fresh payloads
contain short bytecode/name fragments, with maximum printable runs of 48 and
18 bytes. They contain no image, sound, track data or other game assets.
The 3,910 other SWF tags are copied verbatim from the original download.

FFDec exports of the original and reconstructed classes were compared privately:

- `Preloader.Poop0` calls its existing continuation instead of the startup ad.
- `Main` adds the bridge shown in `bridge.as.txt`, registers `gwRead`/`gwStart`,
  and calls `GWPublish` after the existing simulation update.
- `RaceManager.StartRace` increments the bridge attempt counter.
- The three random helpers in `Utils` use `Main.GWRandom`, seeded after start.

FFDec also rewrites equivalent compiler details: unused imports/metadata, an
empty stage conditional, and redundant Number casts. No racing rules change.
`tags.json` pins both original and result payloads. `patch.py` also verifies the
entire decompressed movie against the reference before writing the result.
The readable bridge review extract contains only authored additions.
