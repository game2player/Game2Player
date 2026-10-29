# Players and coding agents

Scout, Builder and the Coding Player are coding agents. They run through Codex CLI
or Claude Code, configured in `catalog/agents/`. The Coding Player writes a code
policy for each game, and that policy then plays. The VLM Player plays from
screenshots through a model configured in `catalog/models/`.

## The loop

Scout checks whether a game can be adapted. Builder captures its runtime, exposes
its state and defines tasks. The VLM Player plays the tasks and reports what goes
wrong, and Builder fixes the environment. The Coding Player then writes the code
policy and sends any environment defect it finds back to Builder. Games that pass
are used to collect trajectories.

## How a player acts

```text
game and task → player harness → ActionPlan → ActionExecutor → game
```

- `harnesses/actions.py`: actions, durations and output format.
- `harnesses/vlm/`: the VLM Player's prompt, history and decision loop.
- `harnesses/policy/`: runs code policies.
- `agents/client.py` and `agents/backends.py`: model requests, over a CLI or HTTP.
- `agents/codecs.py`: turns model output into actions.

Code policies and the VLM Player act through the same executor. The VLM Player sees
only screenshots. Game state goes to the task evaluator, and to code policies
through their probe.

Each VLM action carries a short `note`, kept in the action history, and the model's
native thinking is recorded beside it. By default the history holds the last 50
steps and 6 screenshots, plus the current screenshot. Set `history_steps` and
`images_max` in the actor configuration or a plan's `harness` section. Thinking uses
the profile's first level unless another is chosen. With paused inference the game
waits while the model thinks. With realtime inference it keeps running.

## Add an HTTP model

Copy `catalog/models/http-player.yaml`, set `model` and `endpoint`, pick the codec
your model supports, and add any request fields or thinking levels it needs.
