# Trajectory recording

`play.py run` plays a generated game and records it. With `--task` it plays that
task, and without it the game runs freely. A person plays by default. Code policies,
and the VLM Player without a task, need `--max-steps` or `--duration-s`.

```bash
python play.py run --game 1_example --task 1_01      # you play a task
python play.py run --game 1_example                  # you play freely
python play.py run --game 1_example --task 1_01 --actor policy --headless --max-steps 100
python play.py run --game 1_example --task 1_01 \
  --actor vlm --model http-player --headless --max-steps 100
```

Other options include `--state-fps`, `--video-fps`, `--video-bitrate` and
`--no-stop-on-terminal`.

## Video and GPU

Recordings are AV1 WebM, encoded with FFmpeg's `libsvtav1`. Headed sessions capture
the Chrome tab and headless sessions use Chromium's screencast. Both are aligned to
the input events.

On Linux, `GAMEENV_WEBGL_GPU` picks the NVIDIA GPU that renders WebGL by its PCI bus
ID, which `nvidia-smi --query-gpu=index,pci.bus_id --format=csv` lists.
`GAMEENV_WEBGL_GPUS` spreads batch workers over several GPUs. Policies and video
encoding run on the CPU, so size `--workers` to your CPUs and memory.

## Episode files

- `summary.json`: game, task and player, timing and video details, the catalog text
  used, state coverage, the final result and any errors.
- `video.webm`: the viewport video on the episode clock.
- `inputs.jsonl`: model decisions, executed inputs and browser input events.
- `states.jsonl`: game state, policy state and task evaluation over time.

An `agent_decision` row holds `observation_t`, the execution time `t`, `step`, the
parsed `actions` with their `valid` flags, `note`, native `thinking` and the raw
`model_output`. Invalid actions are recorded without being executed. An
`executed_input` row is a low-level input the executor sent, with its action index.
Browser events carry `is_trusted`.

States are sampled at 5 Hz, plus a final row. Each row has `sample_id`, `t`,
`finalized`, `state`, `policy_state` and `task`. `task` holds the status, score,
best score, progress and metrics, and is null in free play. `policy_state` is filled
only when a code policy plays. The evaluator reads `state`.

## Batch collection

A campaign's collect stage runs the code policy and the VLM Player. A collection
plan can also run on its own:

```bash
python -m tools.rollout run examples/plans/policy_collect.yaml \
  --model policy --out data/trajs/policy --workers 4 --dry-run
```

Set the plan's games and episode targets, then drop `--dry-run` to collect. A rerun
reuses finished recordings. Scheduler state and attempt logs are in `_scheduler/`
under the output folder.
