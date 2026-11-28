These five binaries are complete, independently compiled AVM1 adapter action
lists, each ending with ActionEnd. Each has its own constant pool when needed.
Their entire readable source is in the corresponding `.as` file.

Build provenance: FFDec 26.3.0 `-importScript` against an empty five-frame AS2
SWF (version 9, 25 fps), with one placeholder DoAction per frame. Import these
sources in order as frame_1 through frame_5: loader, play, bridge, live, router.
Extract only the five resulting DoAction payloads. No upstream game file was
used as a compiler input. Installer execution does not invoke FFDec.

Sizes: loader 7, play 2, bridge 1,567, live 132, router 95 bytes.

loader/play replace the two original loader actions; bridge/live/router are
inserted after the respective original actions, preserving the original
constant pools and instructions. Only adapter code is distributed here.
