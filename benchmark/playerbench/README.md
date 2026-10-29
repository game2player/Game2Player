# PlayerBench

PlayerBench contains 50 games and 287 tasks for evaluating gameplay agents. Use
the commands below to build the game environments and evaluate a model. The table
at the end links to each game's source and task definitions.

## Build the environments

Follow the [installation and setup instructions](../../README.md#getting-started)
to install Game2Player and configure your coding agent and VLM Player.

From the repository root, run:

```bash
python -m tools.game_pipeline benchmark/playerbench/campaign.yaml --jobs 2 --until policy
```

The pipeline downloads each game, prepares it for the shared runtime, tests it
through play and creates a code policy. Memory Match and Simon Sequence are loaded
from the source code included in `originals/`. Run the same command again to resume
unfinished work.

The default campaign uses Codex. To use Claude Code, set `agent: claude` and
`vlm_check.model: claude-player` in [campaign.yaml](campaign.yaml).

You can find the generated files in these directories:

| Output | Location |
| --- | --- |
| Local game builds | `games/benchmark/` |
| Generated game and task catalogs | `catalog/games/` and `catalog/tasks/` |
| Generated code policies | `harnesses/policy/policies/` |
| Campaign progress and logs | `.cache/game-pipeline/playerbench/` |

## Evaluate a model

After building the environments, run:

```bash
python -m tools.rollout run benchmark/playerbench/evaluation.yaml \
  --model codex-player --out data/trajs/playerbench --workers 2
```

Replace `codex-player` with the player profile you want to evaluate. Use `--workers`
to choose how many episodes run at once and `--thinking` to set the model's thinking
level. Other evaluation settings are in [evaluation.yaml](evaluation.yaml).

Recordings and episode results are saved in `data/trajs/playerbench/`. Run the same
command again to resume an interrupted evaluation.

To evaluate the released Game2Player models, use vLLM 0.29.0 as described in the
[model card](https://huggingface.co/game2player/Game2Player-4B#run-the-model), then run:

```bash
python -m tools.rollout run benchmark/playerbench/evaluation.yaml \
  --model game2player-4b --gpus 0 --thinking off \
  --out data/trajs/playerbench-4b-off
```

The model is downloaded automatically. Use `--model game2player-9b` for the 9B
model. Use `--thinking on` and a different output directory to evaluate with
thinking. The profiles set the output limit to 512 tokens with thinking off and
32,768 with thinking on. If vLLM is in another environment, add
`--vllm /path/to/inference-env/bin/vllm`.

To use an existing server instead, replace `--gpus 0` with
`--endpoints http://127.0.0.1:8000/v1/chat/completions`. The server's model name
must match the profile: `game2player-4b` or `game2player-9b`.

## Games and tasks

Click a game name to view its tasks or use the source link to open the original game.

| Game | Genre | Tasks | Source |
| --- | --- | ---: | --- |
| [MOBS, INC](tasks/38_mobs-inc.yaml) | Action | 5 | [Original source](https://overboy.itch.io/mobs-inc) |
| [Gun Knight](tasks/61_gun-knight.yaml) | Shooter | 5 | [Original source](https://stepford.itch.io/gun-knight) |
| [Kingdom Rush](tasks/64_kingdom-rush.yaml) | Strategy | 6 | [Original source](https://www.newgrounds.com/portal/view/587194) |
| [Zombotron Re-Boot](tasks/73_zombotron-reboot.yaml) | Action | 9 | [Original source](https://antkarlov.itch.io/zombotron-reboot) |
| [Tiny Kingdom](tasks/80_tiny-kingdom.yaml) | Strategy | 4 | [Original source](https://neltile.itch.io/tiny-kingdom) |
| [Raide](tasks/86_raide.yaml) | Puzzle | 7 | [Original source](https://kultisti.itch.io/raide) |
| [Celeste Classic 2](tasks/89_celeste-classic-2.yaml) | Platformer | 7 | [Original source](https://maddymakesgamesinc.itch.io/celeste-classic-2) |
| [Chess by Sparsh](tasks/100_chess-by-sparsh.yaml) | Puzzle | 6 | [Original source](https://github.com/sparshsam/chess-by-sparsh) |
| [Memory Match](tasks/105_memory-match.yaml) | Puzzle | 6 | [Original source](originals/105_memory-match/) |
| [Simon Sequence](tasks/107_simon-sequence.yaml) | Action | 6 | [Original source](originals/107_simon-sequence/) |
| [SHIFT 2](tasks/125_shift-2.yaml) | Platformer | 7 | [Original source](https://armorgames.com/play/964/shift-2) |
| [NOM NOM - Great Autumn Jam](tasks/134_nom-nom-great-autumn-jam.yaml) | Simulation | 5 | [Original source](https://anaissalla.itch.io/nomnom) |
| [HIT8OX](tasks/142_hit8ox.yaml) | Action | 6 | [Original source](https://vladcomarlau.itch.io/hit8ox) |
| [Pigeon Ascent](tasks/148_pigeon-ascent.yaml) | RPG | 6 | [Original source](https://escada-games.itch.io/pigeon-ascent) |
| [A Dance of Fire and Ice](tasks/167_a-dance-of-fire-and-ice.yaml) | Action | 8 | [Original source](https://fizzd.itch.io/a-dance-of-fire-and-ice) |
| [Auto Necrochess](tasks/174_auto-necrochess.yaml) | Strategy | 5 | [Original source](https://stopsignal.itch.io/auto-necrochess) |
| [Project: Intellisphere](tasks/175_project-intellisphere.yaml) | Shooter | 3 | [Original source](https://stylemistake.itch.io/intellisphere) |
| [Bonnie's Bakery](tasks/190_bonnies-bakery.yaml) | Simulation | 2 | [Original source](https://aislebsoupid.itch.io/bonnies-bakery) |
| [Frogfall](tasks/197_frogfall.yaml) | Platformer | 6 | [Original source](https://kultisti.itch.io/frogfall) |
| [Trichromat](tasks/212_trichromat.yaml) | Puzzle | 5 | [Original source](https://spratt.itch.io/trichromat) |
| [Mindustry Classic](tasks/222_mindustry-classic.yaml) | Strategy | 6 | [Original source](https://anuke.itch.io/mindustry-classic) |
| [A Small World Cup](tasks/224_a-small-world-cup.yaml) | Sports | 4 | [Original source](https://rujogames.itch.io/a-small-world-cup) |
| [Dominoir](tasks/226_dominoir.yaml) | Card Game | 4 | [Original source](https://kawakubo.itch.io/dominoir) |
| [Two-Timin' Towers](tasks/241_two-timin-towers.yaml) | Action | 4 | [Original source](https://z-richman.itch.io/two-timin-towers) |
| [Petal Quest](tasks/246_petal-quest.yaml) | Adventure | 4 | [Original source](https://noelcody.itch.io/petal-quest) |
| [Alfonzo's Bowling Challenge](tasks/252_alfonzo-s-bowling-challenge.yaml) | Other | 7 | [Original source](https://kittenm4ster.itch.io/abc) |
| [Guardian Sphere](tasks/264_guardian-sphere.yaml) | Shooter | 6 | [Original source](https://pixel-boy.itch.io/guardian-sphere) |
| [Packed to the Gills](tasks/299_packed-to-the-gills.yaml) | Action | 7 | [Original source](https://moonlightjammers.itch.io/packed-to-the-gills) |
| [Handshakes](tasks/300_handshakes.yaml) | Puzzle | 6 | [Original source](https://petpumpkin.itch.io/handshakes) |
| [Sulka](tasks/311_sulka.yaml) | Platformer | 5 | [Original source](https://kultisti.itch.io/sulka) |
| [Forward (Game Jam version)](tasks/332_forward.yaml) | Card Game | 6 | [Original source](https://twotinydice.itch.io/forward) |
| [Conservation of Bass](tasks/354_conservation-of-bass.yaml) | Puzzle | 9 | [Original source](https://emlise.itch.io/conservation-of-bass) |
| [Snug](tasks/357_snug.yaml) | Puzzle | 7 | [Original source](https://cookiecrayon.itch.io/snug) |
| [Dusk Child](tasks/363_dusk-child.yaml) | Adventure | 7 | [Original source](https://sophieh.itch.io/dusk-child) |
| [10-103](tasks/367_10-103.yaml) | Action | 10 | [Original source](https://nerdspartan.itch.io/10-103) |
| [William and Sly](tasks/396_william-and-sly.yaml) | Platformer | 6 | [Original source](https://www.newgrounds.com/portal/view/503833) |
| [My Own Hero Party](tasks/410_my-own-hero-party.yaml) | RPG | 6 | [Original source](https://viniflores.itch.io/my-own-hero-party) |
| [Family Reunion: The Appetizer](tasks/413_family-reunion.yaml) | Simulation | 3 | [Original source](https://waibinin.itch.io/family-reunion) |
| [Last Seen Online](tasks/414_last-seen-online.yaml) | Puzzle | 4 | [Original source](https://qwook.itch.io/last-seen-online) |
| [About Time!](tasks/430_about-time.yaml) | Puzzle | 6 | [Original source](https://ameblohsh.itch.io/about-time) |
| [Human Construction Site](tasks/431_humanconstructionsite.yaml) | Action | 3 | [Original source](https://faesu.itch.io/humanconstructionsite) |
| [Pico Puzzle League](tasks/449_pico-puzzle-league.yaml) | Puzzle | 3 | [Original source](https://tvil.itch.io/pico-puzzle-league) |
| [Mechanical Commando 2: Burning Skies](tasks/456_mechanical-commando-2-burning-skies.yaml) | Shooter | 5 | [Original source](https://www.newgrounds.com/portal/view/574778) |
| [Give Up](tasks/457_give-up.yaml) | Platformer | 8 | [Original source](https://armorgames.com/play/13662/give-up) |
| [SkyFyre II](tasks/464_skyfyre-ii.yaml) | Shooter | 6 | [Original source](https://armorgames.com/play/10666/skyfyre-ii) |
| [Offroaders 2](tasks/475_offroaders-2.yaml) | Racing | 5 | [Original source](https://armorgames.com/play/15403/offroaders-2) |
| [Fair Squares](tasks/483_fair-squares.yaml) | Action | 7 | [Original source](https://kindanice.itch.io/fair-squares) |
| [Bright Lancer](tasks/491_bright-lancer.yaml) | Action | 4 | [Original source](https://slo-nod.itch.io/bright-lancer) |
| [Debbie's Diner Derby](tasks/495_debbie-s-diner-derby.yaml) | Racing | 6 | [Original source](https://team-bugulon.itch.io/debbies-diner-derby) |
| [Rhythm Quest (Demo)](tasks/496_rhythm-quest-demo.yaml) | Action | 9 | [Original source](https://ddrkirbyisq.itch.io/rhythm-quest) |
