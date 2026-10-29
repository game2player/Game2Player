# Working in this repository

Run every command with the project's Python environment, which has the packages in
`pyproject.toml`. If `import yaml` or `import playwright` fails, switch to that
environment before suspecting the game.

Scout and Builder write each game to `games/benchmark/<game_id>/`,
`catalog/games/<game_id>.yaml` and `catalog/tasks/<game_id>/`. The Coding Player
writes its policy and probe under `harnesses/policy/`. Skills for each step and
engine are in `.agents/skills/`.

Serve a game with `env.GameLauncher` and run it with `env.BrowserGameManager`. The
launcher handles MIME types and bundle shards, and the manager installs the shared
random, timing and offline-network controls. Playwright supplies Chromium.

Caches and working notes go in `.cache/`, game files in `games/`, and trajectories
and datasets in `data/`. Keep credentials, usernames and private endpoints out of
source files and examples.

Do not commit, push or upload. When you copy a bundled tool or runtime into a game,
keep its license files with it.
