# Builder workflow

Engine-specific extraction lives in the engine skill. This file holds what is true
across engines: the contracts the runner enforces, the hooks it installs behind
your back, and the traps that have already cost runs.

## The canonical state contract

`gameAPI.getState()` is read off the live page by `env/game_state_tracker.py`. The
gate requires these nine keys and checks their types:

| field | requirement |
| --- | --- |
| `schemaVersion` | string |
| `gameId` | string, equal to the catalog id |
| `timestampMs`, `gameTimeMs` | numeric, not booleans |
| `status` | string |
| `is_actionable` | `true` once the game accepts input |
| `terminal` | object with boolean `isTerminal`; `outcome` is `success`, `fail`, or null |
| `game_state`, `metrics` | objects |

Every path any task's evaluator names -- `score_field`, `end_field`, each entry of
`metrics_fields`, and the frozen `progress_signal.field` -- must exist in the first
state after the task's start URL loads. Score and progress
sources must be finite numbers, not booleans. End/diagnostic fields may be
booleans, strings or null as their evaluator contract specifies.

`is_actionable` deserves its own paragraph. `env/browser_manager.py` polls it and
gives up after 60 seconds by default; on timeout the episode is abandoned before any file is
written, so the failure looks like "nothing happened" rather than like a bug. Set
it from a real readiness condition -- the scene is loaded, the first frame has been
simulated, input is bound -- not from a fixed delay, and make sure it survives
reset. It must be `true` at task start or the gate fails outright.

If an unskipped native introduction demonstrably needs longer, set the game's
`readiness_timeout_s` in its catalog entry. Recording and Builder checks use it
for both startup and reset; it does not make an unready state actionable.

## Hooks the launcher installs

These run in every episode whether or not you know about them. Adapters that ignore
them are not wrong, but adapters that fight them are.

- **`env/browser_scripts/deterministic_random.js`** is injected as an init script
  whenever `random_seed` is set. It replaces `Math.random` with a mulberry32 seeded
  from that value and exposes exactly three functions: `window.__getRandomSeed()`,
  `window.__resetRandomSeed()`, and `window.__restoreOriginalRandom()`. Call
  `__resetRandomSeed()` from your `reset()` so a seeded task actually replays; the
  seed itself is fixed by the runner and cannot be changed from the page.
  If the game seeds its own generator, reset that too, or `random_seed` does
  nothing observable.
- **`env/browser_scripts/dynamic_speed_control.js`** implements
  `speed_multiplier` by virtualising `Date.now` and `performance.now`, not by
  skipping frames. A game that reads wall-clock time from another source will run at
  a different speed from the one the catalog declares.
- **`env/browser_scripts/offline_network.js`** enforces `block_external_network`.
  The gate always launches with it on, so anything your build fetches from a remote
  host fails there even if it worked while you were developing.
- **`_headers.json`** in the game folder maps URL paths to response headers.
  `env/game_launcher.py` reads it. Compound extensions -- `.wasm.gz`,
  `.data.unityweb`, `.framework.js.gz` -- are served as generic binary without it,
  which breaks some WebGL loaders and shows up as a page that never boots.

## Entering a task without clicking

Menu clicking is the route people reach for and the one that breaks. It needs pixel
coordinates that a layout change invalidates, and a wait for a scene that has no
observable ready condition -- so it is tuned with `sleep`, and a swallowed click
starts the wrong variant with no error at all. Every engine here has something
better; find it before you write a coordinate.

| engine | the direct entry |
| --- | --- |
| any | a query parameter, seed, or save value the game already honours -- always check this first, in its own source |
| Unity IL2CPP | the method behind the menu button, found in `symbols.sqlite3` and called through `window.__unityBridge.call(tableIndex, args)` or `sendMessage`; see the Unity skill |
| Godot | a GDScript function called from your `JavaScriptBridge` hook, in the same patched PCK that publishes state |
| Construct | `window.cr_getC2Runtime()` then the layout/instance the button would have reached |
| GameMaker | this build's room accessor and global name map, found as the GameMaker skill describes |
| PICO-8 | `poke` the cart's own state bytes, the same channel the GPIO instrumentation uses |
| HTML5 / JS | the game's own module or globals -- read `game_api.js` first; the handle you need is often already resolved there |

Two rules that follow from this:

- **Read the variant back.** Whatever route you take, ask the game which character,
  level or mode actually started and compare it with what you requested. A start that
  cannot be verified is not a deterministic start. `init` and `reset` must reach
  that playable state; the session runner does not call a separate `gameAPI.start()`.
  `is_actionable=true` at a Start menu proves input readiness, not task readiness.
- **A missing ready condition is a signal, not an obstacle.** If you find yourself
  writing `sleep(5000)` because a scene has no observable ready state, that means you
  are driving the game from outside instead of from inside. Go back and find the
  entry point; do not lengthen the sleep.

## Traps that have cost real runs

**The itch.io hotlink guard.** Captured itch embeds carry a `htmlgame.js` that, on
reload, sees a `127.0.0.1` referrer and assigns
`window.location = https://itch.io/embed-hotlink`. The aborted navigation leaves a
white page with a null `gameAPI`, and reset lands on `chrome-error://chromewebdata/`.
Diagnose it by aborting that one URL in Playwright and watching the symptom vanish;
fix it by deleting the script tag, which in these captures sits after `</html>`.
It costs a full diagnose cycle every time it is not recognised on sight.

**Underscore, not hyphen.** The runtime directory must be
`games/benchmark/<game_id>/` with the exact catalog id. Packaging into a hyphenated
directory instead makes the gate report the directory missing before any runtime
check runs, and the repair is moving every packaged file.

**High-water marks break when the game shrinks its own list.** A completion test
derived from a remembered maximum count fails permanently once the game trims that
list itself -- the remembered maximum stays above anything the current state can
reach, and the task can never verify. Derive completion from current state, or latch
the completion event itself, never from a maximum accumulated earlier.

**A wrapper called `gameAPI` is not evidence.** Some captures ship their own object
by that name. Prove each field changes in response to a real event before trusting
it, and prefer the game's own named objects over anything that merely looks
official.

## Checking task completion

The `game_api_metric` evaluator succeeds as soon as the best score reaches the
task's target. `end_field` is another stop condition, not a requirement that must
also be satisfied. Correct `terminal` fields therefore do not prevent an early
score-based success.

If the goal is to activate two switches **and leave the room**, using raw switch
count with target 2 ends the task before the exit. Keep raw switch counts available,
but use a task progress value that stays below its target until the real exit
event. Likewise, boss health reaching zero must not finish a clear-stage task
before the actual stage-clear event. A score or distance milestone, by contrast,
should succeed at its threshold without needing a native win screen.

During EVALUATE, use the loaded task and the real evaluator on states around the
required final event. A source-backed cache-only trigger is sufficient; this is
not a request to solve the task. Check both `status` and `should_stop`:

```python
from env.task_evaluator import build_task_evaluator

evaluate = build_task_evaluator(
    task.evaluator_id, task.evaluator_config,
    start_score=task.task_start_score_field,
    target_score=task.task_target_score_field,
    max_steps=task.max_steps,
    continue_on_fail=task.continue_on_fail,
)
result = await evaluate(state, step_index=0, metrics={})
print(result.status, result.should_stop, result.stop_reason)
```

Use fresh metrics for each independent check: the evaluator remembers a previously
reached target. An unfinished, non-failed state should continue; the actual success
state should stop successfully. If a transition cannot be exercised, report it as
pending under the existing evidence rules. Constructed states can check the scoring
logic, but are not evidence of an observed gameplay transition.

## Playing from the players' side

The EVALUATE player check (the Player view section of the Builder prompt) uses the
runtime players use: the catalog's window size and `env.ActionExecutor` with the
player role's controls, so each action is sent with the same default timing a VLM
or recorded player gets. Write the actions as the guide words the controls; do not
tune them until they work.

```python
import asyncio
from pathlib import Path

from catalog._yaml import load_yaml_mapping
from catalog.games._base import GameDefinition
from env import ActionExecutor, BrowserConfig, BrowserGameManager, GameLauncher
from env.game_launcher import append_url_suffix

async def play_as_player(game_id, suffix, actions, out_dir):
    game = GameDefinition.from_mapping(load_yaml_mapping(Path("catalog/games") / f"{game_id}.yaml"))
    launcher = GameLauncher(game_id)
    manager = BrowserGameManager(BrowserConfig(
        game_url=append_url_suffix(launcher.start(), suffix), width=game.width,
        height=game.height, headless=True, block_external_network=True))
    try:
        await manager.start()
        await manager.wait_until_actionable(stage="player-view")
        executor = ActionExecutor(manager.page, game.player.controls)
        await manager.page.screenshot(path=Path(out_dir) / "start.png")
        for index, action in enumerate(actions):
            await executor.execute(action)
            await asyncio.sleep(1.0)
            await manager.page.screenshot(path=Path(out_dir) / f"after_{index:02d}.png")
    finally:
        await manager.close()
        launcher.stop()

# A guide that says "hold W to charge, left-click to shoot":
asyncio.run(play_as_player("1_example", "?task=1_01", [
    {"action": "mouse_move", "x": 900, "y": 360},
    {"action": "press_key", "key": "w", "duration": 1.0},
    {"action": "click", "x": 900, "y": 360},
], "RUN_DIR/player_view"))
```

Check whether the game's input loop sees a short tap. If it requires a button to
remain down, the guide must describe that hold instead of calling it a click.

## Where to look before inventing an extraction path

Read the game's readable or pretty-printed source, the matching engine skill, and
this run's working notes. No prebuilt game adapters or previous-run notes are
included. Policy Author's skills (`$policy-author` and its
engine specialists) document runtime techniques from the other side of the same
problem and are worth reading when a handle is hard to reach; load them for
reference only, and never adopt their constraints, which forbid touching the game.
