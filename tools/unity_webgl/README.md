# Use Unity WebGL tools

IL2CPP analysis needs .NET Runtime 6.0. Set `GAMEENV_DOTNET` if `dotnet` is not on `PATH`.

## Analyze a build

Replace `1_my-game` with your game ID:

```bash
python -m tools.unity_webgl discover games/benchmark/1_my-game
python -m tools.unity_webgl analyze games/benchmark/1_my-game
```

`analyze` prints the paths of its output files. Use the reported `symbols.sqlite3`
path in these commands:

```bash
python -m tools.unity_webgl query /path/to/symbols.sqlite3 GameManager --kind type
python -m tools.unity_webgl show-type /path/to/symbols.sqlite3 GameManager
```

`--dumper /path/to/Il2CppDumper.dll` and `--dotnet /path/to/dotnet` choose another
analyzer or runtime for `analyze`.

## Read serialized assets

Pass a Unity data file, AssetBundle or serialized asset file:

```bash
python -m tools.unity_webgl assets list /path/to/game.data --type TextAsset
python -m tools.unity_webgl assets list /path/to/game.data --name level
python -m tools.unity_webgl assets show /path/to/game.data --path-id 123 --file sharedassets0.assets
python -m tools.unity_webgl assets show /path/to/game.data --path-id 123 --output .cache/level.json
```

`list` prints the file names and path IDs that `show` takes. These commands use
UnityPy and work without .NET.

## Check a generated profile

```bash
python -m tools.unity_webgl validate-profile catalog/unity/1_my-game.yaml
```

See the [Unity skill](../../.agents/skills/unity-webgl/SKILL.md) for writing profiles
and connecting them to the game's state API.

## Set the cache directory

Analysis files are written to `.cache/unity_webgl/`. Set `GAMEENV_UNITY_CACHE`,
or pass `--cache-dir /path/to/cache` before the subcommand:

```bash
python -m tools.unity_webgl --cache-dir /path/to/cache analyze games/benchmark/1_my-game
```

See the [engine tool guide](../engine_inspect/README.md) for other engines and
[third-party notices](../../THIRD_PARTY_NOTICES.md) for licenses.
