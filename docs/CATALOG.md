# Catalog

Scout, Builder and the Coding Player write these files for each game in a campaign.

| Location | Contents |
| --- | --- |
| `catalog/games/<game_id>.yaml` | Rules, roles, controls and viewport |
| `catalog/tasks/<game_id>/<task_id>.yaml` | Task instruction, start and success condition |
| `catalog/unity/<game_id>.yaml` | How to read state from a Unity game, when needed |
| `games/benchmark/<game_id>/` | The game's browser runtime |
| `harnesses/policy/policies/g<game_id>.py` | Code policy |
| `harnesses/policy/probes/g<game_id>.js` | State probe used by the code policy |

A game or task ID is its file name without `.yaml`. In policy and probe file names,
hyphens in the game ID become underscores: `1_my-game` gives `g1_my_game.py` and
`g1_my_game.js`. A model ID is a file name under `catalog/models/`, or a path to a
YAML file.

## Game

A game has `game_name`, `game_rules`, `width`, `height` and one or more
`game_roles`, with optional `url` and `speed_multiplier`. Each role has a name, a
context for the player and its controls:

```yaml
game_name: 1_example
width: 960
height: 640
game_rules: |
  Move the character to the visible goal.
game_roles:
  - name: player
    context:
      role_description: Control the character.
      control_notes: |
        - Arrow keys move the character.
    controls:
      allowed_keys: [ArrowUp, ArrowDown, ArrowLeft, ArrowRight]
      allow_clicks: false
      hold_duration: 0.2
```

## Task

A task holds the instruction shown to the player and the game state that checks it.
Write `task_prompt` the way a player would read it.

```yaml
task_id: "1_01"
game_id: 1_example
evaluator_id: game_api_metric
task_prompt: Reach the visible goal.
task_start_score_field: 0
task_target_score_field: 1
difficulty: easy
pause_during_inference: true
continue_on_fail: false
evaluator_config:
  score_field: metrics.goal_reached
```

The evaluator reads `evaluator_config.score_field` from the game's `gameAPI` state.
Progress is `(best_score - start) / (target - start)`, clamped to [0, 1], where
`start` and `target` are `task_start_score_field` and `task_target_score_field`.
The task succeeds as soon as the score reaches the target.

Other fields:

- `game_url_suffix` adds a task-specific start parameter to the game URL.
- `aggregate_score_fields` sums several state paths into the score.
- `metrics_fields` copies extra state paths into reports.
- `continue_on_fail` resets and retries after the game ends in failure.
- `difficulty` sets the default step limit, which a run can override.

## Player model

`catalog/models/` has profiles for an OpenAI-compatible vision service and for the
Codex and Claude CLIs. Copy one and set its model and endpoint.

- `backend`: HTTP chat completions, Codex CLI or Claude CLI.
- `codec`: `qwen` for text tool calls with 0–1000 coordinates, `canonical_tool` for
  function calls with pixel coordinates.
- `api_key_env` or `api_key_file`: where the key comes from.
- `thinking`: the API fields for each thinking level.
- `request_options`: extra API fields. `unsupported_params`: fields the service rejects.
- `serve`: local vLLM weights and startup arguments.

## Coding agent

`catalog/agents/codex.yaml` and `catalog/agents/claude.yaml` configure the CLI that
runs Scout, Builder and the Coding Player. `executable` selects the CLI, `model` the
model (the CLI's own default when omitted) and `reasoning_effort` the thinking
effort. Claude reads its token from `credential_file`. Codex uses its own login.
