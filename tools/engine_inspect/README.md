# Use engine tools

These tools take apart a game's files so Builder can find its state.

## Check dependencies

```bash
python -m tools.engine_inspect doctor
```

| Tool | Additional requirement |
| --- | --- |
| Unity IL2CPP analysis | .NET Runtime 6.0 |
| Unity serialized assets | UnityPy, installed by `requirements.txt` |
| Flash analysis and editing | Java 8+ |
| Flash playback | Bundled Ruffle runtime |
| Godot analysis | Linux x86_64 |
| PICO-8 cartridges | Shrinko8, installed by `requirements.txt` |

Java installs with `conda install -c conda-forge openjdk`. For a Java or .NET runtime
outside `PATH`, set `GAMEENV_JAVA` or `GAMEENV_DOTNET` to its executable.

## Inspect a game directory

Replace `1_my-game` with your game ID:

```bash
python -m tools.engine_inspect inspect games/benchmark/1_my-game
```

The output lists files and detected engine markers, including files inside ZIP
shards. Use `--limit 100` to list more files.

## Unity

Follow the [Unity guide](../unity_webgl/README.md) to index IL2CPP symbols or read
serialized assets.

## Flash

Extract ActionScript from a SWF:

```bash
python -m tools.flash -export script .cache/flash-scripts /path/to/game.swf
```

Import edited scripts into a new SWF:

```bash
python -m tools.flash -importScript /path/to/game.swf .cache/patched.swf .cache/flash-scripts
```

Install the browser runtime into a new folder of your game:

```bash
python -m tools.flash ruffle-runtime games/benchmark/1_my-game/ruffle
```

`python -m tools.flash -help` lists the other FFDec commands.

## Godot

```bash
python -m tools.godot --list-files=/path/to/game.pck
python -m tools.godot --recover=/path/to/game.pck --output=.cache/godot-recovered
```

Add `--include=res://scenes/main.gdc` to recover a single script.
`python -m tools.godot --help` lists the other options.

## PICO-8

Replace `player.js` with the exported player's filename:

```bash
python -m tools.pico8 unpack games/benchmark/1_my-game/player.js .cache/pico8/1_my-game
python -m tools.pico8 pack games/benchmark/1_my-game/player.js .cache/pico8/1_my-game
python -m tools.pico8 check games/benchmark/1_my-game/player.js
```

## Tool files and caches

Bundled analyzers are under each engine tool's `vendor/` directory. Extracted
FFDec and GDRE files are stored in `.cache/engine_tools/`; Unity analysis uses
`.cache/unity_webgl/`. Set `GAMEENV_ENGINE_TOOLS`, `GAMEENV_UNITY_CACHE`, or
Unity's `--cache-dir` to use another directory. Set `GAMEENV_FFDEC` or
`GAMEENV_GDRE` to select a different analyzer executable.

See [third-party notices](../../THIRD_PARTY_NOTICES.md) for tool versions,
source links and licenses.
