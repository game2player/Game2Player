/* Conservation of Bass: original GameMaker HTML5 runtime, native level starts and observers.
 * Loaded after gm_48_48.js and before GameMaker_Init. The export itself is untouched;
 * the only page patch is removing itch.io's htmlgame.js hotlink guard from index.html.
 *
 * Minified handles in this build (authored names come from the bundle's own name map):
 *   _P8(objectIndex)  -> live instances      _G6._y7() -> current room index
 *   object 1 obj_gui (_78 focus_gotten), 3 ocontroller, 4 odoor (the glass), 12 oplayer
 *   ocontroller: _28 room_order_index, _N7 room_order_array, _O7 room_par_array (stars,
 *     keyed by room), _J7 game_progress, _y8 state (2 play, 3 dying, 4 transition,
 *     5 area announcer), _48 transition_timer, _v9 par of the current level, _39 room_overide
 *   oplayer: _3a times_warped (swaps this attempt), _y8 state (0 walk, 1 aiming, 3 dead)
 */
(function () {
  "use strict";
  const GAME_ID = "354_conservation-of-bass";
  const SAVE_KEY = "gm_48_48.0.save_data.ini";
  // Room indices of this build, in the bundle's room table order.
  const ROOM_NAMES = ["rintro", "r_br_tutorial_walk", "r_br_easy_u", "r_pr_three_spike_roof",
    "r_gh_tiny_u", "r_gh_tiny_side_climb", "r_gh_one_small_hop", "r_gh_two_and_three",
    "r_gh_big_fish", "r_gh_jump_side", "r_gh_hops", "r_gh_opposite_p", "r_br_tutorial_swap",
    "r_br_look_up", "r_br_up_or_down", "r_pr_two_spike", "r_pr_pyramid", "rmain_menu",
    "r_gh_thin_tower", "r_pr_spike_swap", "r_pr_centre_t", "r_br_tutorial_cant_swap",
    "rgame_5_hard", "rfinal", "r_gh_tutorial_midair", "r_gh_vertical", "r_pr_sidestep",
    "r_pr_spike_up", "r_pr_one_tele", "rlevel_select", "r_gh_tutorial_hold_x",
    "r_bside_one_tele", "r_bside_sidestep", "r_bside_opposite", "r_bside_two_and_three",
    "r_bside_final", "r_br_loopy", "r_br_two_tiles", "r_pr_spike_drag"];
  // Each task: the room-order positions to clear in sequence, and whether a clear only
  // counts when it earns the level's star (swaps within par).
  const TASKS = {
    "354_01": { positions: [1, 2, 3], names: ["r_br_tutorial_walk", "r_br_tutorial_swap", "r_br_look_up"] },
    "354_02": { positions: [4, 5, 6, 7], names: ["r_br_easy_u", "r_br_up_or_down", "r_br_tutorial_cant_swap", "r_br_two_tiles"] },
    "354_03": { positions: [9, 10, 11], names: ["r_gh_tutorial_hold_x", "r_gh_hops", "r_gh_tiny_u"] },
    "354_04": { positions: [14, 15, 16], names: ["r_gh_tutorial_midair", "r_gh_one_small_hop", "r_gh_big_fish"] },
    "354_05": { positions: [21, 22, 23], names: ["r_pr_spike_swap", "r_pr_spike_up", "r_pr_two_spike"] },
    "354_06": { positions: [25, 26, 27], names: ["r_pr_one_tele", "r_pr_sidestep", "r_pr_spike_drag"] },
    "354_07": { positions: [8], names: ["r_br_loopy"], star: true },
    "354_08": { positions: [31], names: ["rgame_5_hard"] },
    "354_09": { positions: [32, 33], names: ["r_bside_sidestep", "r_bside_two_and_three"] }
  };
  const taskId = new URLSearchParams(location.search).get("task") || "354_01";
  const task = TASKS[taskId];
  const CLOSENESS_CAP = 0.9;
  const delay = window.__realSetTimeout || window.setTimeout.bind(window);
  const realNow = window.__realPerfNow || performance.now.bind(performance);
  let initializing = null, resetting = null, startupError = null, hookError = null;
  let run = freshRun();

  function freshRun() {
    return { ready: false, steps: 0, cleared: 0, best: 0, startDistance: null,
      attemptPlayer: null, attemptEligible: true, restarts: 0, clearsOverPar: 0,
      lastClear: null, success: false };
  }
  // Both accessors throw until GameMaker_Init has built the instance and room tables.
  function instances(index) {
    try { return typeof window._P8 === "function" ? window._P8(index) : []; } catch (_) { return []; }
  }
  function room() {
    try { return window._G6 && typeof window._G6._y7 === "function" ? window._G6._y7() : -1; } catch (_) { return -1; }
  }
  function control() { return instances(3)[0] || null; }
  function player() { return instances(12)[0] || null; }
  function glass() { return instances(4)[0] || null; }
  function expectedRoom() {
    const c = control();
    if (!task || !c || run.cleared >= task.positions.length) return -1;
    return Number(c._N7[task.positions[run.cleared]]);
  }
  function hasStar(c, roomIndex) {
    const v = c && c._O7 ? c._O7[roomIndex] : false;
    return v === true || Number(v) === 1;
  }
  function progress() {
    return run.success ? task.positions.length : run.cleared + run.best;
  }

  // Straight-line closeness of Bass to the glass in the current task level, relative
  // to where Bass first stood in that level. Credited only in star-eligible attempts
  // for the star task. Kept below one full level until the native clear event.
  function observeApproach(c, p) {
    if (!run.ready || run.success || room() !== expectedRoom() || Number(c._y8) !== 2) return;
    const g = glass();
    if (!p || !g) return;
    if (run.attemptPlayer !== p) {
      run.attemptPlayer = p;
      run.attemptEligible = true;
    }
    if (task.star && Number(p._3a) > Number(c._v9)) run.attemptEligible = false;
    const d = Math.hypot(p.x - g.x, p.y - g.y);
    if (run.startDistance === null) run.startDistance = Math.max(d, 1);
    if (!run.attemptEligible) return;
    const closeness = Math.max(0, Math.min(1, (run.startDistance - d) / run.startDistance));
    run.best = Math.max(run.best, CLOSENESS_CAP * closeness);
  }

  // ocontroller Step: Bass overlapping odoor increments room_order_index and enters the
  // transition state in this same event; the star (room_par_array) is set there too.
  function beforeStep(c) {
    const p = player();
    return { idx: Number(c._28), state: Number(c._y8), room: room(), player: p,
      swaps: p ? Number(p._3a) : 0 };
  }
  function afterStep(before, c) {
    if (run.ready && !run.success) run.steps++;
    if (before.state === 2 && Number(c._y8) === 3 && run.ready) run.restarts++;
    const cleared = before.state === 2 && Number(c._y8) === 4 && Number(c._28) === before.idx + 1;
    if (cleared && run.ready && !run.success && before.room === expectedRoom()) {
      const star = hasStar(c, before.room);
      run.lastClear = { room: ROOM_NAMES[before.room] || before.room, swaps: before.swaps,
        par: Number(c._v9), star };
      if (task.star && !star) {
        // Over par: the game would move on, so send Bass back to retry this level
        // through the same room-order entry the level select uses.
        run.clearsOverPar++;
        c._28 = task.positions[run.cleared];
      } else {
        run.cleared++;
        run.best = 0;
        run.startDistance = null;
        run.attemptPlayer = null;
        run.success = run.cleared >= task.positions.length;
      }
    } else {
      observeApproach(c, player());
    }
    updateHud();
  }
  // GameMaker parses this object table in GameMaker_Init; replace the controller's
  // Step reference before that so the engine itself calls the wrapper every frame.
  (function installStepHook() {
    const def = window._v1 && window._v1._U2 && window._v1._U2[3];
    if (!def || def._z1 !== "ocontroller" || typeof def._V2 !== "function") {
      hookError = "ocontroller step event not found";
      return;
    }
    const original = def._V2;
    def._V2 = function (self, other) {
      const before = beforeStep(self);
      const result = original.apply(this, arguments);
      try { afterStep(before, self); } catch (error) { console.error(error); }
      return result;
    };
  })();

  // Task starts from a fresh save: no unlocked levels, no stars.
  try { localStorage.removeItem(SAVE_KEY); } catch (_) {}

  async function until(condition, label) {
    const deadline = realNow() + 45000;
    while (!condition()) {
      if (realNow() > deadline) throw new Error("Timed out waiting for " + label);
      await new Promise(resolve => delay(resolve, 20));
    }
  }
  async function begin() {
    startupError = null;
    if (hookError) throw new Error(hookError);
    if (!task) throw new Error("Unknown task " + taskId);
    run = freshRun();
    updateHud();
    if (typeof window.__resumeGame === "function") window.__resumeGame();
    if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
    await until(() => control() && instances(1).length, "native controller");
    const c = control();
    // Fresh save on every start: no stars or unlocks carried over from an earlier episode.
    if (c._O7) for (let i = 0; i < c._O7.length; i++) c._O7[i] = false;
    c._J7 = 0;
    try { localStorage.removeItem(SAVE_KEY); } catch (_) {}
    const target = Number(c._N7[task.positions[0]]);
    if (ROOM_NAMES[target] !== task.names[0]) throw new Error("Room order mismatch: " + ROOM_NAMES[target]);
    const oldPlayer = player();
    // obj_gui only accepts play after the "click window to focus" prompt.
    instances(1)[0]._78 = true;
    // The level-select button's own entry: set room_order_index and start the transition.
    c._39 = -4;
    c._28 = task.positions[0];
    c._48 = 0;
    c._z9 = 0;
    c._y8 = 4;
    await until(() => {
      const p = player();
      return room() === target && Number(c._y8) === 2 && p && p !== oldPlayer &&
        Number(p._y8) === 0 && Number(c._28) === task.positions[0] && glass();
    }, "requested level " + task.names[0]);
    run.ready = true;
    updateHud();
    return getState();
  }
  function start() {
    return begin().catch(error => {
      startupError = String(error.message || error);
      run.ready = false;
      updateHud();
      throw error;
    });
  }

  // Small task tally under the play area: the level in this task, swaps and par.
  let hud = null;
  function updateHud() {
    if (!document.body) return;
    if (!hud) {
      hud = document.createElement("div");
      hud.id = "task-hud";
      Object.assign(hud.style, { position: "fixed", left: "50%", bottom: "4px",
        transform: "translateX(-50%)", padding: "3px 8px", color: "#f4efe0",
        background: "#1d1b26b3", borderRadius: "4px", font: "12px monospace",
        pointerEvents: "none", zIndex: "10", whiteSpace: "nowrap" });
      document.body.appendChild(hud);
    }
    hud.hidden = !run.ready;
    if (!task || !run.ready) return;
    const total = task.positions.length;
    if (run.success) { hud.textContent = "Task complete: " + total + "/" + total + " cleared"; return; }
    const c = control(), p = player();
    const label = task.names[run.cleared].replace(/^r(game)?_(br_|gh_|pr_|bside_)?/, "").replace(/_/g, " ");
    const swaps = p ? Number(p._3a) || 0 : 0, par = c ? Number(c._v9) || 0 : 0;
    hud.textContent = (task.star ? "Star " : "Level " + (run.cleared + 1) + "/" + total + " ") +
      label + " · swaps " + swaps + " · par " + par;
  }

  function getState() {
    const c = control(), p = player(), g = glass(), r = room();
    const inLevel = !!(c && p && r === expectedRoom());
    return {
      schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(),
      gameTimeMs: Math.round(run.steps * 1000 / 60),
      status: startupError ? "error" : !run.ready ? "loading" : run.success ? "success" : "playing",
      is_actionable: run.ready && !startupError,
      terminal: { isTerminal: run.success, outcome: run.success ? "success" : null },
      game_state: {
        room: r, room_name: ROOM_NAMES[r] || null,
        level_position: c ? Number(c._28) : null,
        target_level: task && !run.success ? task.names[run.cleared] || null : null,
        in_target_level: inLevel,
        controller_state: c ? Number(c._y8) : null,
        par: c ? Number(c._v9) : null,
        swaps: p ? Number(p._3a) || 0 : 0,
        star_earned: !!(c && r >= 0 && hasStar(c, r)),
        player: p ? { x: p.x, y: p.y, state: Number(p._y8) } : null,
        glass: g ? { x: g.x, y: g.y } : null,
        saved_progress: c ? Number(c._J7) || 0 : 0,
        last_clear: run.lastClear,
        task_complete: run.success, startup_error: startupError
      },
      metrics: {
        task_progress: task ? progress() : 0,
        levels_cleared: run.cleared,
        levels_required: task ? task.positions.length : 0,
        level_closeness: run.best,
        restarts: run.restarts,
        clears_over_par: run.clearsOverPar
      },
      provenance: {
        "game_state.room": "engine_hook", "game_state.room_name": "engine_hook",
        "game_state.level_position": "internal", "game_state.controller_state": "internal",
        "game_state.par": "internal", "game_state.swaps": "internal",
        "game_state.star_earned": "internal", "game_state.player": "internal",
        "game_state.glass": "internal", "game_state.saved_progress": "internal",
        "game_state.last_clear": "engine_hook", "game_state.task_complete": "engine_hook",
        "metrics.task_progress": "engine_hook", "metrics.levels_cleared": "engine_hook",
        "metrics.level_closeness": "internal", "metrics.restarts": "engine_hook",
        "metrics.clears_over_par": "engine_hook"
      },
      verifier: { task: taskId, required_levels: task ? task.names : [], star_required: !!(task && task.star),
        start_distance: run.startDistance, success: run.success }
    };
  }

  window.gameAPI = {
    version: "1.0",
    capabilities: { supports_seed: false, supports_level_select: true,
      supports_inplace_reset: true, provides_actionable_flag: true },
    init() { return initializing || (initializing = start()); },
    reset() {
      if (resetting) return resetting;
      resetting = (initializing || Promise.resolve()).catch(() => {}).then(() => start());
      return resetting.finally(() => { resetting = null; });
    },
    getState
  };
  window.gameAPI.init().catch(error => console.error("Conservation of Bass startup:", error));
})();
