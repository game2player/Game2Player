# Workflow

## Establish the payload

Confirm the local file is a genuine SWF (`FWS`, `CWS`, or `ZWS`) and identify its
Flash/ActionScript version, dimensions, frame rate, embedded assets, and network
dependencies. Do not accept a portal shell or an error movie as the game.

Install the bundled runtime as described in the skill, then use Ruffle's
[official JavaScript API](https://github.com/ruffle-rs/ruffle/wiki/Using-Ruffle#javascript-api)
with local paths. A minimal launcher pattern is:

```html
<div id="stage"></div>
<script src="ruffle/ruffle.js"></script>
<script>
  const player = window.RufflePlayer.newest().createPlayer();
  document.getElementById("stage").appendChild(player);
  player.ruffle().load("game.swf").catch(console.error);
</script>
```

Adapt player dimensions to the SWF's aspect ratio and controls. Use
`allowScriptAccess: true` in the load options only when a verified
ExternalInterface callback needs page access. Serve the result through
`env.GameLauncher` and run it through `env.BrowserGameManager`.
External networking must be blocked for final validation;
required fonts, audio, level data, and secondary SWFs therefore need local copies
and stable paths.

## Find state

Inspect in this order:

1. Existing ExternalInterface exports and callbacks.
2. Named ActionScript classes/variables for score, level, health, objectives, and
   game-over/win screens.
3. SharedObject save data when it updates at a useful cadence.
4. Stable Ruffle/player DOM state.
5. A small visual estimate, explicitly labelled, only as a last resort.

Search the actual SWF or decompiler output, not only the surrounding JavaScript.
A wrapper that calls an ExternalInterface method does not show that the SWF implements it. After
wiring a candidate field, cause the corresponding game event and record
before/after values.

Use the bundled FFDec entry point, with outputs under the Builder cache:

```bash
python -m tools.flash -export script <run-cache>/scripts /path/to/game.swf
```

For a reviewed script edit, upstream `-importScript <infile> <outfile> <scriptsfolder>`
can produce a patched copy. Java is a setup dependency; do not download a separate
analyzer for each game. The repository's Ruffle runtime remains the playback path.

## Reset and tasks

Prefer level-select screens, native restart paths, or a reviewed SharedObject
snapshot. Avoid a save that silently grants task completion. Natural Flash tasks
often map to a level, race, wave, score threshold, mission, or boss phase.

## Evaluate

Test under the same bundled Ruffle runtime with network blocked. Confirm keyboard
and pointer focus, audio not blocking startup, actual frame advance, and real
input. Evaluate every task with the Builder's common task-contract rules; a terminal
transition may remain pending when progress is verified and no contract is
unsupported. Restart twice to expose stale SharedObject state and asynchronous load
bugs. An API method returning constants, `null`, or wall time is a failure.
