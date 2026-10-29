# Workflow

## Runtime contract

`harnesses/policy/harness.py` reads three things off your policy object:

- `act(policy_state) -> action | list | None`. `None` waits and does not count as a
  step. Actions must come from `CANONICAL_ACTIONS` in `harnesses/actions.py`:
  `click, click_hold, mouse_move, drag, scroll, type, press_key, press_keys,
  set_keys, wait`. `duration_policy` is `optional`, so `duration` is allowed.
- `step_interval_s` (default 0.5, floored at 0.008). Set it explicitly: roughly
  0.01-0.08 for real-time games, 0.2-0.5 for turn-based and UI-driven ones so
  animations settle between decisions.
- `sticky_keys` (default false). When true, a `press_key` carrying an arrow key
  becomes a held `set_keys`, released on the next non-arrow action. It holds one
  key at a time, so a game needing two keys down at once -- glide plus steer,
  thrust plus turn, run plus jump, diagonals -- must leave it false and emit
  `set_keys` itself. Decide which of the two you need in DISCOVER and record it.
  Note what keeps the key down: returning the *same* `press_key` emits nothing and
  the key stays held, while returning `None` releases it.

`reset(context)` is optional and is where per-task parameters go. Do not add a
second module or a registry entry.

The probe is a page init script and must end by installing
`window.__policyProbe = { getState: getState }`; `load_probe` rejects a
file without that name. Give it an `emptyState()` default to return when the game
is not up, so the policy never has to guess whether a missing field means "not
started" or "broken".

## Discover

Establish, before writing anything:

1. The handle for the live game object -- `games/benchmark/<id>/game_api.js` first,
   then page source.
2. Which trusted field each task's evaluator scores, and what actually moves it.
3. Whether the required play is expressible in canonical actions.
4. Whether keys are edge- or level-triggered. A cart that arms a jump only on the
   rising edge does nothing at all while a key is held, and a game that clears its
   key map every frame needs taps rather than holds. Both look identical in the
   state: the character simply never moves.
5. Whether a run can score without you. An auto-running game can post evaluator
   successes with zero steps and zero key events. Know that before you start.

Do not expose concealed state: unopened cells, face-down cards, unspawned entities,
future spawn schedules, or the next RNG draw. Name the boundary explicitly in the
DISCOVER record -- which collections exist in the runtime that the probe will not
read, and why.

## Probe

Everything that needs the page goes here: geometry, entity lists, hitboxes, board
contents, canvas-to-page conversion, and derived predictions -- ray-marching a
projectile through wall bounces to return an intercept, or keeping previous-frame
values in closure to derive angular and linear velocity. That closure state is
necessary, because the policy only sees samples at `step_interval_s`, but treat it
as noisy: a derived rate can swing by a factor of two between samples, and a lead
computed from it will need correcting.

When the object you need is not reachable from anything, catch it while it is being
made. Bundlers that keep gameplay classes inside a closure defeat every route
through a global: the application singleton, a display-list walk, a bounded
own-property scan, an `addEventListener` wrap, a re-hooking `requestAnimationFrame`
getter/setter. What works is wrapping `Function.prototype.call` in the init script,
keeping the `this` from the one call that looks like the game's constructor -- two
cheap properties are usually enough to pick it out of thousands -- and **restoring
the original `call` immediately**, because a permanent wrapper on
`Function.prototype.call` slows down every call the page makes. The general form: a
constructor hands you the instance whether or not anything keeps a reference to it
afterwards, so intercept construction rather than hunting the result. This costs one
turn to try and rules the object in or out, which is worth more than another rung on
a ladder that has already failed twice.

Check the probe's index and coordinate conventions against the engine's, once,
explicitly. An engine with 1-based lists read with 0-based indexing reports every
entity one tile from where it is, and the policy dies repeatedly at the same spot
while every field looks plausible.

## Policy

Keep the decision in Python and small enough to replay offline. The recurring
structural faults:

- **Far lookahead outvoting near clearance.** A horizon long enough to see a distant
  obstacle will steer into a near one, or push further into the wall already being
  touched. Score near clearance separately and do not let the long slices outweigh
  it; shortening the horizon is usually the whole fix.
- **A hard veto with no gradient.** A large fixed penalty on any predicted collision
  freezes the agent wherever every option is inside the penalty -- it sits still for
  the entire episode. A veto that can be true for every option is a freeze.
- **A constant-action rollout.** Scoring a handful of fixed key combos held for a
  fixed span cannot express a manoeuvre that changes shape partway through, such as
  "rise, then move sideways". No amount of tuning fixes the wrong action space.
- **A waypoint recomputed each tick from a boolean that flips.** The target
  oscillates between two positions as a clearance test toggles, and the agent
  oscillates with it. Latch the goal, or hysteresise the test.
- **Chattering a key the game wants held.** Settle first which kind of input each
  key is, because the two want opposite treatment. A key that means rotate one
  step, jump, fire, drop, or slide the board is discrete: a 40 ms press is exactly
  what a person produces, and repeating it at the rate the game accepts is correct.
  A key that steers, moves, or accelerates is continuous: a person holds it down
  and lets go when the intent changes. Only the second kind can chatter, and it is
  a fault when it does -- the frame shows one smooth move while the input log shows
  thirty presses, which is not a demonstration worth learning from.
  Chatter comes from writing the continuous case as a fresh question each poll --
  "is there error right now?" -- so that every brush with the target answers no and
  releases. Carry the decision instead: hold a direction, and change it only when
  the intent changes. Neutral is one of the states you can be in, not what happens
  when this poll has nothing to say. Size any deadband so ordinary hunting around
  the target stays inside a single hold rather than crossing in and out of it.
  Two details decide whether "no change" reaches the browser as a release. Key
  transitions are a diff, so re-issuing the target that is already down emits
  nothing and the key simply stays held -- repetition is free and is the right way
  to hold. But an empty `set_keys` is an instruction to let go, and under
  `sticky_keys` so is returning `None`; with `sticky_keys` false, `None` executes
  nothing at all and leaves the keys as they are. Know which of the three your
  policy is saying.

## Evaluate

Every run needs `--headless`, `--output-root` under the run dir, and
`--duration-s` <= 900:

```
play.py run --game <id> --task <task> --actor policy --headless \
  --output-root <run_dir>/eval-<task>-s42 --duration-s 90 --random-seed 42 \
  --port <free> --control-port <free>
```

Read `summary.json` first, in this order: `actor.steps` and `inputs.count` -- zero
steps with a reported success means the game played itself; then
`states.policy_coverage` (below 1.0 means the probe returned null or threw on some
samples); then the trusted score, resets, and the failure pose.

`states.jsonl` samples `policy_state` at `--state-fps` (default 5 Hz), independent
of `step_interval_s`, so replaying `act()` on those rows is not what ran live: when
the sampling rate beats against the policy's own rhythm, every sampled row can show
`act()` returning `None` while the live policy was acting constantly. Use the rows
to locate the failure, then read the inputs log for what the policy actually chose.

## Diagnose and revise

Name one testable fault and make one change. Before spending an EVALUATE, run the
cheap check: `node --check` on the probe, `load_probe` / `get_policy` to confirm
both load, and a replay of the new logic on the recorded failing sample showing it
now chooses differently.

Separate the three cases:

- **Correctable fault**: a specific state, a specific wrong choice, a mechanism. One
  gate added at the right place can turn a repeated death into a clean pass.
- **Plateau**: two consecutive focused changes fail to beat the best prior run on the
  same frontier. Revert to the best-known version, preserve that behaviour, and move
  the frontier to another task rather than tuning a solved one.
- **Environment defect**: `needs_environment` is for a broken shared contract, and in
  practice that means one of two things. Either trusted `is_actionable` never becomes
  true, the run aborts with `No actionable state ... after 60 seconds`, and no episode
  files are written at all; or play deadlocks partway in and `is_actionable` stays
  false from there. A scored field that can never reach its target is the third.
  Missing entity data belongs in the probe, with one exception: state the game holds
  where no page-level route can reach it -- a PICO-8 cartridge's Lua tables, a Flash
  movie without an object bridge -- is a `request_kind` observation request, once per
  experiment. Searching only one route before asking does not qualify.

## Suitability

Report `unsuitable` on a named blocker, and name it in an earlier turn before you
finish on it.

If the game holds legitimate observable state that the probe cannot reach without
changing the game, use the available observation request before concluding that
the missing state makes a policy unsuitable. Do not request concealed state or
repeat a request that already failed to provide a usable channel.

- **The live game object is unreachable.** Every route through a global fails and the
  construction intercept above finds nothing. Signal: `ready=false` in every
  `policy_state` sample of every episode, and zero key events.
- **The probe cannot see what the decision depends on.** Classifying game objects
  from canvas pixels sits on a knife edge: tightening the classifier destroys the one
  working move, loosening it makes the policy act on the wrong object, and revisions
  trade one for the other indefinitely. When the game holds that state but no page
  route reaches it, spend the experiment's observation request before settling here.
- **The observable is too coarse for the required precision.** Real state for most of
  the world plus a synthesised value for the one quantity the manoeuvre turns on will
  fail at the same place every time, however many revisions follow.
- **The action model cannot express the manoeuvre.** Exact hitboxes do not help when
  the available actions cannot produce the required trajectory -- when a jump's peak
  simply never reaches the ledge it must clear.

Two or three failed variants of one idea are enough. Do not run a fifth version of a
technique whose mechanism you have already ruled out.
