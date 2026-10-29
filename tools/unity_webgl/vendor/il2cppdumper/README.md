# Il2CppDumper

Third-party static analyser for IL2CPP builds, used by
`python -m tools.unity_webgl analyze` to produce the `symbols.sqlite3` that the
`query` and `show-type` commands read. Upstream:
<https://github.com/Perfare/Il2CppDumper> (MIT; see `LICENSE`).
The bundled Mono.Cecil 0.11.4 assemblies are covered by `LICENSE.Mono.Cecil.txt`.

With .NET Runtime 6.0 installed, run from the repository root:

```bash
python -m tools.unity_webgl analyze games/benchmark/1_my-game
```

Replace `1_my-game` with your game ID. See the [Unity guide](../../README.md)
for querying the resulting symbol database. To switch versions, replace the DLLs,
`config.json` and license files together.
