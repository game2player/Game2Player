# Inspected ActionScript patch

`abc-ranges.json` reconstructs the DoABC payload at zero-based SWF tag 568.
It references byte ranges in the decompressed original tag, with explicit
insertions from `abc-insert.bin`. Original and result hashes are checked.
The complete decompressed output hash is also checked against the reference.

The result contains 225,691 bytes reused from the download and 19,331 inserted
bytes: compiled bridge instructions, constant-pool additions, compiler
relocations and metadata. The longest insertion is 909 bytes. This is a patch
to uncompressed ActionScript, not a diff against the compressed SWF container.
Every other tag (783 of 784), including all artwork, sounds and embedded levels,
is copied unchanged. All 11 ActionScript strings of at least 128 bytes reuse
their complete original byte ranges; none is included as an inserted literal.

The two affected classes are the root `§_-4v§` and game controller `§_-8t§`.
`bridge-additions.as` is a readable review extract of the new root helper
methods, exported from the reference with JPEXS FFDec 26.3.0, plus a summary
of the controller call sites. It omits original game methods. The additions
register state, native startup, collision geometry and visible-policy snapshot
callbacks, publish state at EXIT_FRAME, configure the frozen initial floor,
and observe native exit/completion events. The existing wrapper implements
progress, reset and pause/resume without changing their behavior.

The binary recipe reproduces the existing modified reference exactly after
decompression. Installation uses only Python standard-library SWF framing,
hashing and zlib; FFDec and Java are not installation dependencies.
